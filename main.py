"""B.LEEGUE streams service — game listings, M3U playlist, and XMLTV EPG."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from sources import bleague
from sources.bleague.proxy import (
    forward_to_cdn,
    handle_playkey,
    proxied_stream_url,
)

log = logging.getLogger("bleague")


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[reportUnknownMemberType]
    await bleague.startup()
    yield
    await bleague.shutdown()


app = FastAPI(lifespan=lifespan)  # type: ignore[reportUnknownMemberType]

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/diagnostic")
async def health_diagnostic():
    """Deep trace through the entire B.LEEGUE pipeline to find where games disappear."""
    import time
    start = time.time()
    
    results = {}
    try:
        # Step 1: Raw day schedule fetch
        raw_day = await bleague.fetch_day_schedule("2026-10-15")
        raw_month = await bleague.fetch_month_schedule("202610")
        results["raw_day_count"] = len(raw_day)
        results["raw_month_count"] = len(raw_month)
        
        # Combine all games
        all_games = []
        seen_ids = set()
        for g in raw_day:
            mid = g.get("game_id")
            if mid and mid not in seen_ids:
                all_games.append(g)
                seen_ids.add(mid)
        for g in raw_month:
            mid = g.get("game_id")
            if mid and mid not in seen_ids:
                all_games.append(g)
                seen_ids.add(mid)
        
        results["combined_count"] = len(all_games)
        
        # Step 2: Show league distribution
        leagues = {}
        for g in all_games:
            ln = g.get("league_name", "?")
            leagues[ln] = leagues.get(ln, 0) + 1
        results["league_distribution"] = leagues
        
        # Step 3: Filter by _is_b_league
        b_league = [g for g in all_games if bleague._is_b_league(g)]
        results["b_league_filtered"] = len(b_league)
        results["b_league_sample"] = [{"id": g.get("game_id"), "league": g.get("league_name"), "flag": g.get("game_end_f")} for g in b_league[:5]]
        
        # Step 4: Compute phases on b_league games
        with_phases = []
        for g in b_league:
            phase = bleague.compute_phase(g)
            flag = g.get("game_end_f", "?")
            with_phases.append({
                "id": g.get("game_id"),
                "phase": phase,
                "flag": flag,
                "has_timestamps": bool(g.get("live_start_date") and g.get("live_end_date") and g.get("missed_live_end_date")),
            })
        
        phase_dist = {}
        for p in with_phases:
            phase_dist[p["phase"]] = phase_dist.get(p["phase"], 0) + 1
        results["phase_distribution"] = phase_dist
        
        # Step 5: Filter by LIVE_LIST_PHASES
        live_only = [p for p in with_phases if p["phase"] in bleague.LIVE_LIST_PHASES]
        results["live_filter"] = len(live_only)
        
        # Step 6: Get team catalog
        idx = await bleague.fetch_team_catalog()
        results["teams_indexed"] = len(idx)
        
        # Step 7: Enrich first game
        if b_league:
            sample_g = b_league[0]
            enriched = bleague.enrich_game(sample_g, idx, "TEST")
            home_idx_key = sample_g.get("home_team_id")
            home_found = home_idx_key in idx if home_idx_key else False
            away_idx_key = sample_g.get("away_team_id")
            away_found = away_idx_key in idx if away_idx_key else False
            results["enrichment_sample"] = {
                "match_id": sample_g.get("game_id"),
                "home_team_id": home_idx_key,
                "home_in_catalog": home_found,
                "home_name_from_idx": idx.get(home_idx_key, {}).get("name_jp", "NOT FOUND") if home_found else "KEY NOT IN CATALOG",
                "home_name_from_row": sample_g.get("home_team_name", ""),
                "away_team_id": away_idx_key,
                "away_in_catalog": away_found,
            }
        
        results["elapsed_sec"] = round(time.time() - start, 2)
        
    except Exception as e:
        results["error"] = str(e)
        import traceback
        results["traceback"] = traceback.format_exc()[-500:]  # cap length
        results["elapsed_sec"] = round(time.time() - start, 2)
    
    return results


@app.get("/health/diagnostic")
async def health_diagnostic():  # type: ignore[reportUnknownMemberType]
    """Raw connectivity test — direct HTTP calls to all upstream endpoints."""
    import time
    import httpx
    
    start = time.time()
    results = {}
    
    headers = {
        "User-Agent": "okhttp/4.9.3",
        "X-OS-TYPE": "ANDROID",
        "X-APP-VERSION": "4.0.2",
        "X-BASKETLIVE-ACCESSTOKEN": "-",
    }
    
    c = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(15.0))
    
    # Raw endpoint tests with exact HTTP status codes
    tests = [
        ("day_schedule", "https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261015"),
        ("team_catalog", "https://api.basketball.mb.softbank.jp/api/v3/teams"),
        ("game_detail", "https://api.basketball.mb.softbank.jp/api/v9/game/507231"),
        ("playkey_no_credential", "https://basketball.mb.softbank.jp/api/v1/playkey"),
        ("playkey_with_freekey", "https://basketball.mb.softbank.jp/api/v1/playkey"),
    ]
    
    freekey_headers = {**headers, "X-BASKETLIVE-FREEKEY": "Jc3_6cnTpWfZxoUPaBUgtHolu4p_xl72uyMkoRevA5Be6h_OshXi3ztp7PCOkA7Y"}
    
    for name, url in tests:
        try:
            req_headers = freekey_headers if name == "playkey_with_freekey" else headers
            req = httpx.Request("GET", url, headers=req_headers)
            resp = await c.send(req)
            body_preview = resp.text[:150].replace('"', '\\"')
            results[name] = {
                "http_status": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "body_length": len(resp.content),
                "body_preview": body_preview,
            }
        except httpx.HTTPError as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            results[name] = {
                "http_error": type(e).__name__,
                "http_status_from_error": status_code,
                "detail": str(e)[:200],
            }
        except Exception as e:
            results[name] = {
                "unexpected_error": type(e).__name__,
                "detail": str(e)[:200],
            }
    
    await c.aclose()
    elapsed = round(time.time() - start, 2)
    results["elapsed_sec"] = elapsed
    
    return results


@app.get("/games/live")
async def games_live() -> dict[str, Any]:  # type: ignore[reportUnknownVariableType]
    """Today's live and upcoming B.LEAGUE men's games."""
    games = await bleague.fetch_live_games()
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(games),
        "games": games,
    }


@app.get("/games/completed")
async def games_completed() -> dict[str, Any]:  # type: ignore[reportUnknownVariableType]
    """Recently completed B.LEAGUE men's games with catch-up URLs."""
    games = await bleague.fetch_completed_games()
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(games),
        "games": games,
    }


@app.get("/stream/{match_id}")
async def stream_proxy(match_id: str, request: Request) -> Any:  # type: ignore[reportUnknownVariableType]
    """Resolve the HLS stream for a match and redirect to it via our CDN proxy.

    Redirecting through the proxy (not straight to Akamai) is what lets the
    #EXT-X-KEY URI be rewritten to /playkey, so the player can decrypt.
    """
    url = await bleague.resolve_stream_for_match(match_id)
    if url is None:
        return JSONResponse(
            status_code=404,
            content={"error": "no_stream", "match_id": match_id},
        )
    proxied = proxied_stream_url(url, str(request.base_url))
    if proxied is None:
        log.warning("Stream URL for %s is outside the CDN base; redirecting directly", match_id)
        proxied = url
    return RedirectResponse(url=proxied, status_code=302)


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------


def _team_name(team: dict) -> str:
    """English club name from the team catalog, falling back to Japanese if absent."""
    return team.get("name_en") or team.get("name_es") or team.get("name_jp") or "?"


def _game_label(game: dict, include_score: bool = True, show_local_time: bool = False) -> str:
    home = game.get("home_team") or {}
    away = game.get("away_team") or {}
    if not home.get("id") and not away.get("id") and game.get("title"):
        # Rows with no teams (TV shows, overseas tour games) only carry a title.
        label = game["title"]
    else:
        label = f"{_team_name(home)} vs {_team_name(away)}"
    if include_score:
        parts = []
        if home.get("score"):
            parts.append(str(home["score"]))
        if away.get("score"):
            parts.append(str(away["score"]))
        if parts:
            label += f" ({'-'.join(parts)})"
    if show_local_time:
        local = game.get("start_time_local", "")
        if local:
            label += f" - {local}"
    else:
        rnd = game.get("round")
        if rnd:
            label += f" - R{rnd}"
    return label


def _channel_id(game: dict, vtype: str | None = None) -> str:
    gid = game.get("match_id", "")
    if vtype:
        return f"{gid}.{vtype}"
    return gid


def _m3u_entry(group: str, title: str, url: str, logo: str | None = None, tvg_id: str | None = None) -> str:
    attrs = f'group-title="{group}"'
    if tvg_id:
        attrs += f' tvg-id="{tvg_id}"'
    if logo:
        attrs += f' tvg-logo="{logo}"'
    return f'#EXTINF:-1 {attrs},{title}\n{url}'


VIDEO_TYPE_LABELS = {"full_game": "Full Game"}


@app.get("/pl/", response_class=PlainTextResponse)
@app.get("/pl",  response_class=PlainTextResponse)
async def playlist(request: Request):  # type: ignore[reportUnknownParameterType, reportUnknownMemberType]
    """IPTV-compatible M3U playlist with B.LEAGUE games."""
    live_games = await bleague.fetch_live_games()
    completed_games = await bleague.fetch_completed_games()

    base_url = str(request.base_url).rstrip("/")
    lines = [f"#EXTM3U url-tvg=\"{base_url}/pl/epg.xml\""]

    # Live / upcoming games
    for game in sorted(live_games, key=lambda g: g.get("start_time_utc", "")):
        mid = game.get("match_id")
        if not mid:
            continue
        logo = (game.get("home_team") or {}).get("logo_url")
        cid = _channel_id(game)
        phase = game.get("computed_phase", "")

        group_name = game.get("conference", "B.LEAGUE")
        if phase == "PLAYING":
            group = f"{group_name} Live"
        else:
            group = f"{group_name} Upcoming"

        url = f"{base_url}/stream/{mid}"
        lines.append(_m3u_entry(group, _game_label(game, show_local_time=True), url, logo, tvg_id=cid))

    # Completed games with videos
    # Completed games also go via /stream/{id} so playback runs through the
    # key-rewriting CDN proxy (a raw Akamai URL can't be decrypted by the player).
    for game in sorted(completed_games, key=lambda g: g.get("start_time_utc", ""), reverse=True):
        videos = game.get("videos")
        mid = game.get("match_id")
        if not videos or not mid:
            continue
        logo = (game.get("home_team") or {}).get("logo_url")
        label = _game_label(game, include_score=False)
        for vtype in VIDEO_TYPE_LABELS:
            vid = videos.get(vtype)
            if vid and vid.get("stream_url"):
                cid = _channel_id(game, vtype)
                group = f"{game.get('conference', 'B.LEAGUE')} Completed"
                lines.append(_m3u_entry(group, f"{label} [{VIDEO_TYPE_LABELS[vtype]}]", f"{base_url}/stream/{mid}", logo, tvg_id=cid))

    return PlainTextResponse("\n".join(lines) + "\n", media_type="audio/x-mpegurl")


# ---------------------------------------------------------------------------
# EPG helpers
# ---------------------------------------------------------------------------


PROGRAMME_DURATION = timedelta(hours=2)


def _dt_to_xmltv(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S") + " +0000"


def _add_clipped(tv: Element, cid: str, title: str, desc: str, start: datetime, stop: datetime,
                 window_start: datetime, window_end: datetime, logo: str | None = None) -> None:
    """Add a programme clipped to the EPG window; skip it if nothing is left."""
    start, stop = max(start, window_start), min(stop, window_end)
    if start < stop:
        _add_programme(tv, cid, title, desc, start, stop, logo)


def _add_programme(tv: Element, cid: str, title: str, desc: str, start: datetime, stop: datetime, logo: str | None = None):  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    prog = SubElement(tv, "programme", start=_dt_to_xmltv(start), stop=_dt_to_xmltv(stop), channel=cid)
    t = SubElement(prog, "title", lang="en")
    t.text = title
    d = SubElement(prog, "desc", lang="en")
    d.text = desc
    cat = SubElement(prog, "category", lang="en")
    cat.text = "Basketball"
    if logo:
        SubElement(prog, "icon", src=logo)


def _game_desc(game: dict, phase: str = "upcoming") -> str:
    home = _team_name(game.get("home_team") or {})
    away = _team_name(game.get("away_team") or {})
    venue = game.get("venue")

    venue_info = f" at {venue}" if venue else ""
    season = game.get("season_name", "")
    rnd = game.get("round")
    match_info = ""
    if rnd:
        # fixture_type is the competition (e.g. "B.LEAGUE PRE-SEASON GAME");
        # only mention it when it differs from the league itself.
        ftype = game.get("fixture_type") or ""
        if ftype and ftype != season:
            match_info = f" {ftype} round {rnd} match"
        else:
            match_info = f" round {rnd} match"
    season_info = f" of the {season}" if season else ""

    if phase == "upcoming":
        line = f"{home} take on {away}{venue_info}."
        time_str = game.get("start_time_local", "")
        if time_str:
            line += f" Tune in at {time_str}."
        if match_info:
            line += match_info + season_info + "."
    elif phase == "live":
        line = f"{home} are taking on {away}{venue_info}"
        if match_info:
            line += f" in this{match_info}{season_info}."
        else:
            line += "."
    else:  # completed
        line = f"{home} took on {away}{venue_info}"
        if match_info:
            line += f" in this{match_info}{season_info}."
        else:
            line += "."
    return line


def _build_epg(live_games: list[dict], completed_games: list[dict], window_start: datetime, window_end: datetime) -> bytes:  # type: ignore[reportUnknownVariableType]
    tv = Element("tv", attrib={"generator-info-name": "bleague"})

    for game in live_games:
        cid = _channel_id(game)
        home = game.get("home_team") or {}
        logo = home.get("logo_url")
        base_title = _game_label(game, include_score=False, show_local_time=True)

        ch = SubElement(tv, "channel", id=cid)
        dn = SubElement(ch, "display-name")
        dn.text = base_title
        if logo:
            SubElement(ch, "icon", src=logo)

        start_str = game.get("start_time_utc", "")
        if not start_str:
            continue
        game_start = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        game_end = game_start + PROGRAMME_DURATION

        _add_clipped(tv, cid, f"Upcoming: {base_title}", _game_desc(game, "upcoming"), window_start, game_start, window_start, window_end, logo)
        _add_clipped(tv, cid, base_title, _game_desc(game, "live"), game_start, game_end, window_start, window_end, logo)
        _add_clipped(tv, cid, f"Completed: {base_title}", _game_desc(game, "completed"), game_end, window_end, window_start, window_end, logo)

    for game in completed_games:
        videos = game.get("videos")
        if not videos:
            continue
        base_title = _game_label(game, include_score=False)
        desc = _game_desc(game, "completed")

        for vtype, vtype_label in VIDEO_TYPE_LABELS.items():
            vid = videos.get(vtype)
            if vid and vid.get("stream_url"):
                cid = _channel_id(game, vtype)
                title = f"{base_title} [{vtype_label}]"
                home = game.get("home_team") or {}
                logo = home.get("logo_url")

                ch = SubElement(tv, "channel", id=cid)
                dn = SubElement(ch, "display-name")
                dn.text = title
                if logo:
                    SubElement(ch, "icon", src=logo)

                # Catch-up is on demand, so it fills the whole guide window
                # (the original tip-off is usually before the window starts).
                _add_programme(tv, cid, title, desc, window_start, window_end, logo)

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(tv, encoding="unicode").encode("utf-8")


@app.get("/pl/epg.xml")
async def epg():
    live_games = await bleague.fetch_live_games()
    completed_games = await bleague.fetch_completed_games()
    now = datetime.now(timezone.utc)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = (window_start + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    xml = _build_epg(live_games, completed_games, window_start, window_end)
    return Response(content=xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Stream proxy (AES-128 license key + CDN forwarding)
# ---------------------------------------------------------------------------


@app.get("/playkey")
async def playkey(request: Request):
    """Serve the 16-byte AES-128 decryption key by injecting the FREEKEY credential."""
    return await handle_playkey(request)


@app.api_route("/{path:path}")
async def catch_all_cdn(path: str, request: Request) -> Response:
    """Forward /ch{NNN}/... to the Akamai CDN, rewriting #EXT-X-KEY URIs.

    Explicit routes are registered first and win; forward_to_cdn 404s any
    path that isn't a CDN channel path.
    """
    return await forward_to_cdn(path, request)


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
