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
import re

import httpx
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi import Request
from starlette.background import BackgroundTask

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


PLAYKEY_URI = 'URI="https://basketball.mb.softbank.jp/api/v1/playkey"'

# CDN paths we forward: /ch{NNN}/... under CDN_BASE. Anything else is a 404.
_CDN_PATH_RE = re.compile(r"^ch\d+/")

# Upstream headers safe to pass through unchanged on a raw (unmodified) body.
_PASSTHROUGH_HEADERS = ("content-type", "content-length", "content-encoding",
                        "cache-control", "last-modified", "etag")


def proxied_stream_url(cdn_url: str, base_url: str) -> str | None:
    """Map a CDN playlist URL onto this service, e.g.
    https://...akamaized.net/live/ch914/ndvr_MAN02.m3u8 -> {base_url}/ch914/ndvr_MAN02.m3u8

    The playlists use relative URIs, so once the player loads the master
    playlist from our host, every variant playlist and segment comes back
    through forward_to_cdn() too. Returns None for URLs outside CDN_BASE.
    """
    if not cdn_url.startswith(_bleague.CDN_BASE):
        return None
    return f"{base_url.rstrip('/')}/{cdn_url[len(_bleague.CDN_BASE):]}"


def rewrite_key_uris(m3u8_text: str, base_url: str) -> str:
    """Rewrite #EXT-X-KEY URI inside a media playlist text.
    
    Replaces the softbank license server URI with our /playkey endpoint.
    Only the URI attribute changes; IV and all other directives are preserved.
    """
    new_uri = f'URI="{base_url.rstrip("/")}/playkey"'
    patched = m3u8_text.replace(PLAYKEY_URI, new_uri)
    if patched != m3u8_text:
        log.info("Rewrote KEY URI in playlist")
    return patched


async def forward_to_cdn(request_path: str, request: Request) -> Response:
    """Forward /ch{NNN}/... to the Akamai CDN, patching playlists if needed.

    Playlists are buffered so the KEY URI can be rewritten; segments are
    streamed through with their upstream body and headers untouched.
    """
    c = _bleague.client
    if c is None:
        return JSONResponse(status_code=502, content={"error": "not_initialized"})
    
    cdn_path = request_path.lstrip("/")
    if not _CDN_PATH_RE.match(cdn_path):
        return JSONResponse(status_code=404, content={"error": "not_found"})
    
    url = _bleague.CDN_BASE + cdn_path
    if request.url.query:
        url += "?" + request.url.query
    
    # Use clean headers for CDN (no auth headers)
    fwd_headers = {"User-Agent": "python-urllib/3"}
    
    try:
        req = c.build_request("GET", url, headers=fwd_headers, timeout=15.0)
        resp = await c.send(req, stream=True, follow_redirects=True)
    except httpx.HTTPError as e:
        log.error("CDN forward failed for %s: %s", request_path, e)
        return JSONResponse(
            status_code=502,
            content={"error": "cdn_unavailable", "path": request_path},
        )
    
    ct = resp.headers.get("Content-Type", "")
    is_m3u8 = cdn_path.split("?")[0].endswith(".m3u8") or "mpegurl" in ct.lower()
    
    if is_m3u8:
        try:
            await resp.aread()
        finally:
            await resp.aclose()
        # resp.text is already decompressed, so upstream Content-Length /
        # Content-Encoding no longer apply; Response sets its own length.
        body = rewrite_key_uris(resp.text, str(request.base_url))
        headers = {}
        if "cache-control" in resp.headers:
            headers["Cache-Control"] = resp.headers["cache-control"]
        return Response(
            content=body.encode("utf-8"),
            status_code=resp.status_code,
            headers=headers,
            media_type=ct or "application/vnd.apple.mpegurl",
        )
    
    # Segment: stream the raw bytes, so upstream length/encoding headers stay valid.
    headers = {k: v for k, v in resp.headers.items() if k.lower() in _PASSTHROUGH_HEADERS}
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=headers,
        background=BackgroundTask(resp.aclose),
    )


async def handle_playkey(request: Request) -> Response:
    """Serve the 16-byte AES-128 decryption key."""
    key = await get_playkey()
    if not key:
        return JSONResponse(status_code=502, content={"error": "key_fetch_failed"})
    return PlainTextResponse(content=key, media_type="binary/octet-stream")
