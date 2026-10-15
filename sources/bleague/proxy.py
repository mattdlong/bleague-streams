"""Stream proxy for B.LEAGUE encrypted HLS feeds.

B.LEAGUE uses AES-128 segment encryption via SoftBank's /api/v1/playkey
license server. Third-party players (VLC, mpv, TiviMate) cannot attach
custom headers to key requests, so we proxy:

  1. Rewrite #EXT-X-KEY URIs from softbank -> our /playkey endpoint
  2. Serve the 16-byte AES-124 key by forwarding the FREEKEY credential
  3. Forward raw segments unchanged (encrypted bytes pass through)

This mirrors the pattern used by https://github.com/mattdlong/nbl1-streams
for its streaming endpoints, adapted for B.LEEGUE's different architecture.
"""
import logging

import httpx
from cachetools import TTLCache
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi import Request

import sources.bleague as _bleague

log = logging.getLogger("bleague.proxy")

# ---------------------------------------------------------------------------
# Playkey cache (shared across all instances; static 16-byte key)
# ---------------------------------------------------------------------------

_playkey_key_cache: bytes | None = None


async def get_playkey() -> bytes:
    """Obtain the 16-byte AES-128 license key.
    
    Cached at module level for the lifetime of this process.
    The key is identical for all games/channels/qualities.
    """
    global _playkey_key_cache
    
    c = _bleague.client
    if c is None:
        return b""
    
    if _playkey_key_cache is not None:
        return _playkey_key_cache
    
    req = httpx.Request(
        "GET",
        "https://basketball.mb.softbank.jp/api/v1/playkey",
        headers={
            "User-Agent": "okhttp/4.9.3",
            _bleague.FREEKEY_HEADER: _bleague.FREEKEY_VALUE,
        },
    )
    try:
        resp = await c.send(req)
    except httpx.HTTPError as e:
        log.error("Playkey request failed: %s", e)
        return b""
    
    assert resp.status_code == 200
    key = resp.content
    assert len(key) == 16, f"Expected 16-byte key, got {len(key)}"
    _playkey_key_cache = key
    return key


def rewrite_key_uris(m3u8_text: str, service_host: str) -> str:
    """Rewrite #EXT-X-KEY URI inside a media playlist text.
    
    Replaces the softbank license server URI with our local /playkey endpoint.
    Only the URI attribute changes; IV and all other directives are preserved.
    """
    old_uri = 'URI="https://basketball.mb.softbank.jp/api/v1/playkey"'
    new_uri = f'URI="http://{service_host}/playkey"'
    patched = m3u8_text.replace(old_uri, new_uri)
    if patched != m3u8_text:
        log.info("Rewrote KEY URI in playlist")
    return patched


async def forward_to_cdn(request_path: str, request: Request) -> Response:
    """Forward an incoming path to Akamai CDN, patching playlists if needed."""
    c = _bleague.client
    if c is None:
        return JSONResponse(status_code=502, content={"error": "not_initialized"})
    
    # Path handling: distinguish absolute vs relative URIs
    if request_path.startswith("/ch"):
        cdn_path = request_path[1:]  # strip leading '/'
    else:
        cdn_path = f"ch/{request_path.lstrip('/')}"
    
    url = _bleague.CDN_BASE + cdn_path
    
    # Use clean headers for CDN (no auth headers)
    fwd_headers = {"User-Agent": "python-urllib/3"}

    
    try:
        resp = await c.get(url, follow_redirects=True, timeout=15.0, headers=fwd_headers)
    except httpx.HTTPError as e:
        log.error("CDN forward failed for %s: %s", request_path, e)
        return JSONResponse(
            status_code=502,
            content={"error": "cdn_unavailable", "path": request_path},
        )
    
    ct = resp.headers.get("Content-Type", "")
    body = resp.content
    
    # Check if this is an M3U8 playlist that needs rewriting
    is_m3u8 = ".m3u8" in request_path or ("mpegURL" in ct.lower())
    if is_m3u8:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            pass  # not text, skip rewrite
        else:
            # Use host from request for accurate redirect target
            service_host = request.url.hostname or "127.0.0.1"
            port = request.url.port or ""
            full_host = f"{service_host}:{port}" if port else service_host
            
            rewritten = rewrite_key_uris(text, full_host)
            if rewritten != text:
                body = rewritten.encode("utf-8")
    
    return Response(
        content=body,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=ct,
    )


async def handle_playkey(request: Request) -> Response:
    """Serve the 16-byte AES-128 decryption key."""
    key = await get_playkey()
    if not key:
        return JSONResponse(status_code=502, content={"error": "key_fetch_failed"})
    return PlainTextResponse(content=key, media_type="binary/octet-stream")


async def handle_stream_proxy(match_id: str, request: Request) -> Response:
    """Resolve and redirect to the HLS stream URL for a match."""
    url = await _bleague.resolve_stream_for_match(match_id)
    if url is None:
        return JSONResponse(
            status_code=404,
            content={"error": "no_stream", "match_id": match_id},
        )
    return RedirectResponse(url=url, status_code=302)
