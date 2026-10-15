# B.LEAGUE Streams Service — Implementation Plan

Project: `bleague-streams` (FORK of `mattdlong/nbl1-streams`)
Deployment target: Google Cloud Run — `asia-northeast1` (Japan)
Reference: `spec.md`, `nbl1.md`, `SCHEDULE.md`

This plan breaks the specification into sequential implementation phases with concrete tasks, deliverables, acceptance criteria, and estimated effort. Each phase is independently testable before moving to the next.

---

## 1. Overview

| Phase | Name | Duration | Depends On |
|-------|------|----------|------------|
| **P0** | Foundation & scaffolding | 1 day | — |
| **P1** | Upstream data fetching | 1 day | P0 |
| **P2** | Business logic — phase computation | 1 day | P1 |
| **P3** | Stream proxy infrastructure | 2 days | P2 |
| **P4** | Output generation — JSON APIs | 1 day | P2 |
| **P5** | Output generation — M3U playlist | 1 day | P4 |
| **P6** | Output generation — XMLTV EPG | 1 day | P4 |
| **P7** | Containerization & deployment | 1 day | P4–P6 |
| **P8** | Testing & hardening | 2 days | P3–P6 |
| **P9** | Polish & monitoring | 1 day | All |
| **P-0** | Repository cleanup (post-implementation) | 0.5 day | After P9 |
| **P-1** | Local development environment setup | 0.5 day | Immediately (parallel with Phase P0 scaffolding) |

**Total estimated effort:** ~12 working days, ~2–3 weeks including buffer. Parallelizable where indicated.

---

## 10. Development Environment Setup

Before writing any code, configure the local Python environment: install dependencies, verify imports work, confirm the app boots cleanly with stub implementations returning empty results.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P1.1 | Create virtual environment | `python -m venv .venv && source .venv/bin/activate` | High |
| P1.2 | Install requirements | `pip install -r requirements.txt` (fastapi, httpx, cachetools, pycryptodome, etc.) | High |
| P1.3 | Verify imports resolve | `python -c "from sources import bleague; print('OK')"` — no errors or missing modules | High |
| P1.4 | Boot FastAPI server locally | `uvicorn main:app --host 0.0.0.0 --port 8080`; in another terminal curl `localhost:8080/health` → `{"status":"ok"}` | High |
| P1.5 | Verify JSON endpoints return 200 with empty arrays | `curl localhost:8080/games/live` → `{"generated_at":...,"count":0,"games":[]}`; same for `/games/completed` | High |
| P1.6 | Verify M3U playlist renders valid output | `curl localhost:8080/pl/` → single line `#EXTM3U url-tvg="http://..."\n` (empty because no games yet) | Medium |
| P1.7 | Verify EPG renders valid XML | `curl localhost:8080/pl/epg.xml` → valid XMLTV root `<tv>` element with no child programs | Medium |
| P1.8 | Add `.gitignore` | Include `__pycache__/`, `.venv/`, `*.pyc`, `.DS_Store` | Low |

### Acceptance criteria

All three outputs (`/games/live`, `/pl/`, `/pl/epg.xml`) return valid format even when the bleague source returns empty lists. This proves the rendering pipeline works end-to-end before any upstream API calls are wired in.

---

## 2. Phase P0 — Foundation & Scaffolding

### Goal
Get a running FastAPI application that responds to `/health`, has the source module structure in place, and can be containerized. No data fetched yet — pure plumbing.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P0.1 | Fork nbl1-streams repo | Clone `mattdlong/nbl1-streams`. Rename to `bleague-streams`. Keep all NBL1 source modules temporarily (will remove later). | High |
| P0.2 | Create `sources/bleague/__init__.py` | Empty file. Define package structure matching nbl1 pattern. Export stub functions: `startup()`, `shutdown()`, `fetch_live_games()`, `fetch_completed_games()`. | High |
| P0.3 | Update `main.py` — import bleague | Add `from sources import bleague` to imports. Register `await bleague.startup()` / `await bleague.shutdown()` in lifespan. | High |
| P0.4 | Remove non-bleague sources from main.py (optional) | Comment out ninenow, sbs, plex, samsung, auftv imports and their lifespan hooks. Revert when adding back other sources later. | Medium |
| P0.5 | Set up virtual environment & install deps | `python -m venv .venv`; `pip install fastapi uvicorn[standard] httpx cachetools google-cloud-firestore pycryptodome` | High |
| P0.6 | Verify app starts | `uvicorn main:app --host 0.0.0.0 --port 8080`; curl `localhost:8080/health` → `{"status":"ok"}` | High |
| P0.7 | Set up git repo | `git init`, `.gitignore`, initial commit | Low |

### Deliverable
Running local FastAPI app at port 8080 with empty bleague source plugged into the app lifecycle. Healthcheck returns 200.

### Acceptance Criteria
```bash
$ curl localhost:8080/health
{"status": "ok"}

$ ls sources/bleague/__init__.py
sources/bleague/__init__.py
```

---

## 3. Phase P1 — Upstream Data Fetching

### Goal
Implement HTTP client and fetch functions for schedule, teams, and game detail endpoints. All calls use the correct auth headers, run over httpx.AsyncClient, and return parsed JSON dicts. No business logic yet — raw upstream data only.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P1.1 | Define constants in `sources/bleague/__init__.py` | `CDN_BASE`, `API_BASE`, `AUTH_HEADERS`, `LEAGUE_IDS`, `B_LEAGUE_NAMES`, `FREEKEY_HEADER`, `FREEKEY_VALUE` as **hardcoded literal strings** in source, matching the NBL1 pattern (where `Authorization: Bearer xxx.app` is a compile-time constant, not env-var). No environment variables or secret manager for upstream credentials. Auth headers include `User-Agent: okhttp/4.9.3`, `X-OS-TYPE: ANDROID`, `X-APP-VERSION: 4.0.2`, `X-BASKETLIVE-ACCESSTOKEN: - `. | High |
| P1.2 | Implement `startup()` | Create `httpx.AsyncClient(headers=AUTH_HEADERS, timeout=15.0)`. Log info message. Match NBL1 pattern exactly. | High |
| P1.3 | Implement `shutdown()` | Close httpx client. Log shutdown. Match NBL1 pattern. | High |
| P1.4 | Implement `fetch_json(url, cache=None)` wrapper | Thin async wrapper around `client.get()`. Returns `resp.json()` or raises `UpstreamError`. Applies TTLCache if provided. Throttled by `upstream_sem = asyncio.Semaphore(20)`. | High |
| P1.5 | Implement day schedule fetch | `GET /api/v5/schedules?league_id=ALL&game_date={YYYYMMDD}`. Parse response: top-level keys `games[]` and `schedule[]`. Return list of game dicts. Cached with 60s TTL. | High |
| P1.6 | Implement month schedule fetch | `GET /api/v5/schedules/month?league_id=ALL&game_month={YYYYMM}`. Parse response: each entry has `date`, `date_order`, `games[]`. Flatten inner games arrays. Cached with 60s TTL. | Medium |
| P1.7 | Implement team catalog fetch | `GET /api/v3/teams`. Parse `teams_info[].items[].teams[]`. Build index: `{team_id: {name_jp, name_en, name_es}}`. Cached with 3600s TTL (teams rarely change). | High |
| P1.8 | Implement game detail fetch | `GET /api/v9/game/{game_id}`. Parse `game` object: `game_end_f`, `live_urls[]`, `missed_urls[]`, team names, venue. Not cached (state changes per request). | High |
| P1.9 | Implement B.LEAGUE filter | Function that filters game rows by `league_name` containing `B.LEAGUE ONE` or `B.LEAGUE PREMIER`. Excludes Wリーグ and other non-men's entries. Return boolean predicate. | High |
| P1.10 | Unit test: day schedule fetch | Mock HTTP response. Call day schedule function. Verify parsing yields dict list with expected keys (`game_id`, `game_end_f`, `home_team_id`, `away_team_id`, etc.). | High |
| P1.11 | Unit test: team catalog fetch | Mock HTTP response with B.LEAGUE teams. Call catalog function. Verify index contains correct English names mapped by `team_id`. | High |
| P1.12 | Integration test: real API call (from Japan region) | Execute actual `GET /api/v5/schedules?league_id=ALL&game_date={today}`. Verify 200 response, parseable JSON, correct content-type. Note: requires Japan-region network. | Medium |

### Deliverable
Functions that fetch raw upstream data and return structured Python dicts. Auth headers applied correctly. Caching in place with appropriate TTLs.

### Acceptance Criteria
```python
# Manual verification after writing code
>>> await startup()
>>> games = await fetch_day_schedule("20261014")
>>> len(games) >= 0  # may be 0 if off-season
>>> b_league_only = [g for g in games if is_b_league_game(g)]
>>> teams_index = await fetch_team_catalog()
>>> assert 709 in teams_index  # IWATE BIGBULLS
>>> detail = await fetch_game_detail("507231")
>>> "live_urls" in detail["game"]
True
```

---

## 4. Phase P2 — Business Logic: Phase Computation

### Goal
Compute game status (phase) from `game_end_f` and three timestamps. Filter games into live/upcoming vs completed categories. Resolve streams for qualifying games. Produce canonical game dictionaries.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P2.1 | Implement `compute_phase(game_row)` | Per spec Section 3. Three cases: (a) missing timestamps → flag-only mapping; (b) present timestamps → 7-step clock evaluation; (c) cancelled → immediate. Returns one of: `IN_FUTURE`, `IN_FUTURE_ONE_DAY`, `PLAYING`, `FINISHED_MISSED_MATCH`, `FINISHED`, `CANCELLED`. Handle GMT+9 shift. Test boundary conditions (exactly-on-boundary = not inside window). | High |
| P2.2 | Implement `select_stream_list(phase, game_end_f, live_urls, missed_urls)` | Per spec Section 5.2 decision matrix. Returns `live_urls`, `missed_urls`, or `None`. | High |
| P2.3 | Implement `pick_quality(quality_list, preset="Middle")` | Find first item whose `quality` field matches display label for preset. Returns dict with `quality` and `url`, or `None`. Case-sensitive exact match. | High |
| P2.4 | Implement `resolve_stream_for_game(game_id)` | Calls game detail endpoint, applies `select_stream_list`, then `pick_quality`. Returns CDN URL string or `None`. Never caches. | High |
| P2.5 | Implement `enrich_game(game_row, team_index)` | Look up home/away teams in catalog index. Populate `name_jp`, `name_en`, `name_es`. Also build `start_time_utc` from `game_date + game_time` (already JST, convert to UTC). Attach computed phase. | High |
| P2.6 | Implement `process_conference_live()` | Query today's day schedule. Filter to B.LEAGUE. Compute phase for each game. Include if phase ∈ `LIVE_PHASES` (PLAYING, FINISHED_MISSED_MATCH, IN_FUTURE, IN_FUTURE_ONE_DAY). For PLAYING and FINISHED_MISSED_MATCH, resolve stream. Enrich with team data. Return filtered list. | High |
| P2.7 | Implement `process_conference_completed()` | Query month schedules for lookback window (3 days). Filter to B.LEAGUE. Compute phase. Include only FINISHED. Resolve stream from missed_urls (or live_urls fallback). Categorize into `full_game` catch-up type. Return filtered list. | High |
| P2.8 | Implement `build_canonical_game(game_row, team_data, phase, stream_url, video_type)` | Build dict matching schema in spec Section 20. Ensure all fields present, null-safe. | High |
| P2.9 | Implement `fetch_live_games()` | Call `process_conference_live()` → get today's active games. Sort by `start_time_utc` ascending. Attach `conference` label. Return list. | High |
| P2.10 | Implement `fetch_completed_games()` | Call `process_conference_completed()` → get recent finished games. Sort by `start_time_utc` descending. Attach `conference` label. Return list. | High |
| P2.11 | Unit test: compute_phase with all flags | Test each `game_end_f` value ("9", "0", "1", "99") with various timestamp combinations. Verify correct phase output for all 7 evaluation branches. | High |
| P2.12 | Unit test: select_stream_list decision matrix | Test all condition combinations against the table. Verify correct list returned. | High |
| P2.13 | Unit test: pick_quality mapping | Test each preset code (8M, 4M, 1.5M, 500K, blank) maps to correct quality label string. Test mismatch (unknown code → None). | High |
| P2.14 | Integration test: full pipeline end-to-end | Call `fetch_live_games()`, verify returned games have correct phases, resolved streams, enriched team names. Compare against manually verified data. | High |
| P2.15 | Edge case: game_end_f outside known values | Verify graceful fallback behavior (falls through clock rules; epoch-parse makes it unlikely but defensive check recommended). | Low |

### Deliverable
Working `fetch_live_games()` and `fetch_completed_games()` functions that return properly classified, enriched, and sorted game lists with stream URLs where applicable.

### Acceptance Criteria
```python
>>> await startup()
>>> live = await fetch_live_games()
>>> assert all(g["computed_phase"] in LIVE_PHASES for g in live)
>>> assert all(g["stream_url"] is not None for g in live if g["computed_phase"] in ("PLAYING", "FINISHED_MISSED_MATCH"))
>>> completed = await fetch_completed_games()
>>> assert all(g["computed_phase"] == "FINISHED" for g in completed)
>>> assert len(live) + len(completed) > 0  # some games exist on query date
```

---

## 5. Phase P3 — Stream Proxy Infrastructure

### Goal
Implement the three proxy endpoints that bridge third-party players to encrypted B.LEEGUE streams: `/playkey`, media playlist rewriter, and segment forwarder.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P3.1 | Implement `/playkey` endpoint in `main.py` | `GET /playkey`. Forward request to `https://basketball.mb.softbank.jp/api/v1/playkey` with FREEKEY credential header. Return `Plain Response` with binary/octet-stream body (16 bytes). | High |
| P3.2 | Implement `_rewrite_key_uris(m3u8_text)` helper | Scan M3U8 text for `URI="https://basketball.mb.softbank.jp/api/v1/playkey"` and replace with `URI="http://{service-host}/playkey"`. Only modify the URI attribute. Leave IV untouched. | High |
| P3.3 | Implement path router for CDN assets | Route incoming requests: paths starting with `/ch{NNN}/` → strip leading `/`, prepend CDN base. Paths without `/ch{NNN}/` prefix → prepend `ch{NNN}/`. Construct full Akamai URL. | High |
| P3.4 | Implement `forward_to_cdn(request_path)` handler | Forward to Akamai CDN via httpx. If response is M3U8 (`.m3u8` or `mpegURL` content-type), apply `_rewrite_key_uris()`. Otherwise passthrough binary. Respect timeouts. | High |
| P3.5 | Wire up CDN asset routes in `main.py` | Catch-all route `/{path:path}` that checks if path looks like a CDN resource (starts with `/ch` or `.m3u8` or segment number). Route to `forward_to_cdn()`. Return 404 for unknown paths. | High |
| P3.6 | Implement `/stream/{game_id}` endpoint | `GET /stream/{game_id}`. Call `resolve_stream_for_game(game_id)` from bleague source. If result is not None, return `RedirectResponse(url=result, status_code=302)`. If None, return `JSONResponse(status_code=404, content={"error": "no_stream", "match_id": game_id})`. | High |
| P3.7 | Test playkey endpoint locally | Start server. `curl http://localhost:8080/playkey | xxd` → verify 16 bytes. Compare hex output against known key. | High |
| P3.8 | Test playlist rewrite | Request a master playlist through the proxy. Verify `#EXT-X-KEY` line now points to `/playkey` instead of softbank URL. | High |
| P3.9 | Test segment forwarding | Request a segment through the proxy. Verify 200 response with `Content-Type: video/MP2T`. Save to file, verify decryptable with AES-128 key. | High |
| P3.10 | Geo-restriction validation | Confirm all outbound requests originate from Japan-region instance (if deploying to Cloud Run). Document the geo-block behavior (`403` without Japan egress). | Medium |

### Deliverable
All three proxy endpoints operational. VLC can connect to `/playkey` and receive the AES-128 key. Media playlists are rewritten to point the key URI at the service host.

### Acceptance Criteria
```bash
# Playkey returns 16-byte key
$ curl http://localhost:8080/playkey | wc -c
16

# Playlist rewrite confirmed
$ curl http://localhost:8080/ch902/ndvr_MAN02.m3u8 | grep EXT-X-KEY
#EXT-X-KEY:METHOD=AES-128,URI="http://127.0.0.1:8888/playkey",IV=...

# Redirect works for existing game
$ curl -sI http://localhost:8080/stream/507231 | head -3
HTTP/1.1 302 Found
Location: https://basketlive-live-sni.akamaized.net/live/ch902/ndvr_MAN02.m3u8
Content-Length: 0

# 404 for nonexistent game
$ curl http://localhost:8080/stream/999999
{"error":"no_stream","match_id":"999999"}
```

---

## 6. Phase P4 — Output Generation: JSON APIs

### Goal
Expose `/games/live` and `/games/completed` endpoints using the business logic from Phase P2. Return properly structured JSON responses.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P4.1 | Implement `/games/live` endpoint | `GET /games/live`. Call `bleague.fetch_live_games()`. Wrap in response envelope: `{generated_at: ..., count: ..., games: [...]}`. Return JSONResponse. | High |
| P4.2 | Implement `/games/completed` endpoint | `GET /games/completed`. Call `bleague.fetch_completed_games()`. Same envelope structure. Games with no videos excluded. | High |
| P4.3 | Add error handling wrapper | Catch `UpstreamError` from both endpoints. Return 502 JSON with appropriate error detail. Per spec Section 14. | High |
| P4.4 | Verify JSON response schema | Validate output matches spec Section 20 canonical dictionary. All required fields present. Null fields where appropriate. | High |
| P4.5 | Performance benchmark | Measure response time from cold start. Verify concurrent requests don't deadlock. Profile memory usage under load. | Medium |

### Deliverable
Two JSON API endpoints returning correctly structured game data. Error responses follow spec format.

### Acceptance Criteria
```json
// GET /games/live response
{
    "generated_at": "2026-10-14T10:30:00Z",
    "count": 5,
    "games": [{
        "match_id": "507231",
        "international_ref": "507231",
        "game_end_f": "0",
        "computed_phase": "PLAYING",
        "start_time_utc": "2026-10-14T10:05:00Z",
        "start_time_local": "19:05",
        "round": "5",
        "fixture_type": "REGULAR",
        "season_name": "2026 B.LEAGUE ONE",
        "venue": "盛岡タカヤアリーナ",
        "conference": "B.LEAGUE ONE",
        "home_team": {
            "id": "709",
            "name_jp": "岩手ビッグブルズ",
            "name_en": "IWATE BIGBULLS",
            "name_es": "IWATE",
            "score": "54",
            "logo_url": "https://..."
        },
        "away_team": {
            "id": "715",
            "name_jp": "アースフレンズ東京Z",
            "name_en": "EARTHFRIENDS TOKYO Z",
            "name_es": "TOKYO-Z",
            "score": "48",
            "logo_url": "https://..."
        },
        "live_urls": [...],
        "missed_urls": [],
        "stream_url": "https://basketlive-live-sni.akamaized.net/live/ch902/ndvr_MAN02.m3u8"
    }]
}
```

---

## 7. Phase P5 — Output Generation: M3U Playlist

### Goal
Build the M3U playlist endpoint `/pl/` that combines BLeague games with any future non-BLeague sources (currently just BLeague alone, extensible).

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P5.1 | Implement `_game_label(game, show_local_time=True)` | Generate human-readable label with Japanese team names, score (if available), round info. Format: `Home vs Away (H-A) - DD/MM HH:MM`. | High |
| P5.2 | Implement `_channel_id(game, vtype=None)` | Generate `tvg-id`: `{match_id}` or `{match_id}.{vtype}` for categorised videos. | High |
| P5.3 | Implement `_m3u_entry(group, title, url, logo=None, tvg_id=None)` | Generate M3U tag: `#EXTINF:-1 group-title="{group}" tvg-id="{id}" tvg-logo="{logo}",{title}` followed by URL. | High |
| P5.4 | Implement `playlist_endpoint()` in `main.py` | `GET /pl/`. Gather live games, completed games, (future: TV channels). Build M3U lines. Order: (1) ABC/FTA channels, (2) 9Now, (3) SBS, (4) Racing, (5) News, (6) Plex, (7) Samsung, (8) Live games (ordered), (9) Upcoming games (ordered), (10) Completed with videos. Return `PlainTextResponse(content_type="audio/x-mpegurl")`. | High |
| P5.5 | Implement BLeague game grouping | Apply `group-title` per spec Section 10. Groups: `{League} Live`, `{League} Catch-Up`, `{League} Upcoming`, `{League} Completed`. Use Japanese conference name. | High |
| P5.6 | Implement stream URL embedding strategy | Live games: proxy redirect. Completed games: embedded CDN URL. Upcoming: proxy redirect (may 404). | High |
| P5.7 | Verify playlist syntax | Parse generated M3U with an m3u parser library. Verify no malformed tags. Check all URIs are absolute. Verify `url-tvg` attribute if EPG URL included. | Medium |
| P5.8 | Local test with VLC | Serve playlist locally. Open in VLC. Verify all live/catch-up entries attempt connection. Verify upcoming entries gracefully fail. | High |

### Deliverable
Valid M3U playlist with BLeague game entries properly grouped and ordered. Compatible with IPTV players.

### Acceptance Criteria
```m3u8
#EXTM3U url-tvg="http://127.0.0.1:8080/pl/epg.xml"
#EXTINF:-1 group-title="B.LEAGUE ONE Live" tvg-id="507231",岩手 vs アースフレンズ (54-48) - 14/10 19:05
http://127.0.0.1:8080/stream/507231
#EXTINF:-1 group-title="B.LEAGUE ONE Completed" tvg-id="507230.full_game",岩手 vs アースフレンズ [Catch-Up]
https://basketlive-live-sni.akamaized.net/live/ch901/ndvr_MAN02.m3u8
#EXTINF:-1 group-title="B.LEAGUE ONE Upcoming" tvg-id="507232",岩手 vs アースフレンズ - R6
http://127.0.0.1:8080/stream/507232
```

---

## 8. Phase P6 — Output Generation: XMLTV EPG

### Goal
Build the XMLTV EPG endpoint `/pl/epg.xml` with BLeague game programme slots alongside any future TV channel guides.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P6.1 | Implement `_dt_to_xmltv(dt)` | Convert datetime to XMLTV format: `YYYYMMDDHHMMSS +0000`. Always UTC. | High |
| P6.2 | Implement `_add_programme(tv_elem, cid, title, desc, start, stop, logo=None)` | Append `<programme>` element to parent `<tv>` element. Includes title (lang="en"), category="Basketball", optional icon. | High |
| P6.3 | Implement `_game_desc(game, phase)` | Generate description text varying tense per phase. Uses Japanese team names. Includes venue, round, season context. | High |
| P6.4 | Implement EPG window calculation | Window spans from today 00:00 UTC to day-after 00:00 UTC. Two-day total coverage. | High |
| P6.5 | Implement `_build_epg(live_games, completed_games, window_start, window_end)` | Build XML tree. For each live game: create `<channel>` element + three `<programme>` elements (Upcoming, Live, Completed). For each completed game with videos: create `<channel>` per video type + one `<programme>`. | High |
| P6.6 | Handle JST→UTC conversion in EPG | Convert `game_date + game_time` (JST) to UTC for all XMLTV timestamps. Subtract 9 hours. | High |
| P6.7 | Implement `/pl/epg.xml` endpoint | `GET /pl/epg.xml`. Gather live/completed games. Call `_build_epg()`. Return `Response(content=xml_bytes, media_type="application/xml")`. | High |
| P6.8 | Validate XML structure | Parse output with xml.etree.ElementTree or xmllint. Verify valid XMLTV schema. Check channel IDs match EPG IDs in M3U playlist. | Medium |
| P6.9 | Verify programme durations | Spot-check: all programme blocks are ~2 hours. Verify they don't overlap incorrectly. | Low |

### Deliverable
Valid XMLTV EPG with BLeague programme entries. Channel IDs correlate with M3U `tvg-id` attributes.

### Acceptance Criteria
```xml
<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="bleague">
    <channel id="507231">
        <display-name>IWATE vs EARTHFRIENDS - 19:05</display-name>
        <icon src="https://..."/>
    </channel>
    <programme channel="507231" start="20261014000000 +0000" stop="20261014100500 +0000">
        <title lang="en">Upcoming: IWATE vs EARTHFRIENDS</title>
        <desc lang="en">岩手ビッグブルズ take on アースフレンズ東京Z at 盛岡タカヤアリーナ.</desc>
        <category>Basketball</category>
    </programme>
    <programme channel="507231" start="20261014100500 +0000" stop="20261014120500 +0000">
        <title lang="en">IWATE vs EARTHFRIENDS - 19:05</title>
        <desc lang="en">岩手ビッグブルズ are taking on アースフレンズ東京Z in this round 5 match.</desc>
        <category>Basketball</category>
    </programme>
    ...
</tv>
```

---

## 9. Phase P7 — Containerization & Deployment

### Goal
Package the service into a Docker container, create deployment scripts, and deploy to Google Cloud Run in the Japan region.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P7.1 | Write Dockerfile | `FROM python:3.12-slim`. Install deps. Copy source. EXPOSE 8080. CMD uvicorn. Skip playwright chromium (not needed for Bleague). | High |
| P7.2 | Write requirements.txt | fastapi, uvicorn, httpx, cachetools, google-cloud-firestore, pycryptodome. Pin versions per spec. | High |
| P7.3 | Create deploy.sh | gcloud run deploy with `--region asia-northeast1`, `--allow-unauthenticated`, `--memory 2Gi`, `--cpu 2`, `--min-instances 1`, `--timeout 120`. **No env vars needed** — FREEKEY is hardcoded in source. Matches NBL1 which only passes `SBS_EMAIL` via env var (for optional browser-authenticated source); B.LEEGUE has zero env-var dependencies. | High |
| P7.4 | Create `.dockerignore` | Exclude `.git/`, `__pycache__/`, `.venv/`, local test files. | Medium |
| P7.5 | Create GCP project & enable APIs | `gcloud projects create`. Enable Cloud Run API. (Secret Manager not needed — no secrets used.) | High |
| P7.6 | Set up credentials | `gcloud auth login`. Configure default project. Set up service account with Cloud Run access role. | High |
| P7.7 | Build and push Docker image | `docker build -t gcr.io/$PROJECT/bleague-streams:latest .`. Push to Google Container Registry. | High |
| P7.8 | Deploy to Cloud Run | Run deploy.sh. Verify service comes online. Curl health endpoint via Cloud Run URL. | High |
| P7.9 | Configure custom domain (optional) | Map custom hostname to Cloud Run. Set up HTTPS certificate. | Low |
| P7.10 | Set up billing alerts | Configure GCP budget alerts to prevent unexpected costs. | Medium |

### Deliverable
Service deployed on `asia-northeast1` Cloud Run instance, accessible via public HTTPS URL. All endpoints responding.

### Acceptance Criteria
```bash
$ SERVICE_URL=$(gcloud run services describe bleague-streams --region asia-northeast1 --format 'value(status.url)')
$ curl "$SERVICE_URL/health"
{"status":"ok"}

$ curl "$SERVICE_URL/games/live" | python3 -m json.tool
{"generated_at":"...","count":5,"games":[...]}
```

---

## 10. Phase P8 — Testing & Hardening

### Goal
Write comprehensive tests, fix edge cases, add retry logic, improve error messages, and ensure production readiness.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P8.1 | Write pytest fixtures | Shared mocks for HTTP responses: schedule day, schedule month, team catalog, game detail, playkey. Reusable across all test modules. | High |
| P8.2 | Write unit tests for business logic | Tests for `compute_phase()`, `select_stream_list()`, `pick_quality()`, `enrich_game()`. Cover all boundary conditions from spec. | High |
| P8.3 | Write integration tests | End-to-end tests using real API calls (requires Japan-region execution). Verify response shapes, field presence, data consistency. | High |
| P8.4 | Write proxy tests | Test `/playkey` endpoint returns exact 16 bytes. Test playlist rewrite preserves all directives except KEY URI. Test segment forwarding preserves binary integrity. | High |
| P8.5 | Write EPG validation tests | Parse generated XMLTV with lxml. Verify schema compliance. Test channel ID correlation between M3U and EPG outputs. | High |
| P8.6 | Add retry logic for transient failures | Implement exponential backoff retry decorator for upstream calls (up to 3 retries, jitter). Classify retryable errors (connection reset, 503) vs fatal (400 bad request, 403 forbidden). | High |
| P8.7 | Add circuit breaker pattern (optional) | Track consecutive upstream failures per endpoint. If threshold exceeded, return cached/stale data rather than failing fresh. | Low |
| P8.8 | Handle W-League exclusion explicitly | Add explicit skip for Wリーグ, W联赛, and other non-B.LEAGUE entries. Log skipped leagues for debugging. | Medium |
| P8.9 | Fix trailing whitespace wire key issue | Ensure JSON parsing strips `schedule_class_nameE ` (with trailing space) correctly. If manual parsing used, handle extra whitespace. | Low |
| P8.10 | Performance testing under load | Simulate 10, 50, 100 concurrent requests. Monitor latency, memory, CPU. Verify semaphore throttling prevents upstream overload. | Medium |
| P8.11 | Logging & observability | Structured logging with request IDs. Metrics: upstream call counts, success/failure rates, response latencies. | Medium |
| P8.12 | Health check enhancement | Add `/health/detailed` endpoint that reports uptime, cache sizes, last fetch times, upstream connectivity status. | Low |

### Deliverable
Test suite covering all critical paths. Retry logic for resilient upstream communication. Production-grade logging and metrics.

### Acceptance Criteria
```bash
$ pytest tests/ -v
==== 87 passed, 0 failed, 0 skipped ====

$ curl -s $SERVICE_URL/health/detailed
{"uptime_seconds": 86400, "cache_sizes": {"data_cache": 256}, "last_fetch": {"day": "2026-10-14T07:00:00Z"}, "upstream_healthy": true}
```

---

## 11. Phase P9 — Polish & Monitoring

### Goal
Final documentation, monitoring setup, incident response procedures, and optional feature additions.

### Tasks

| # | Task | Details | Priority |
|---|------|---------|----------|
| P9.1 | Write README.md | Project overview, architecture diagram, quick-start guide, deployment instructions, troubleshooting section. | High |
| P9.2 | Add CHANGELOG.md | Track versions, breaking changes, new features, bug fixes. | High |
| P9.3 | Configure Cloud Run monitoring | Enable Cloud Run built-in metrics dashboard. Set up alerting policies for error rate spikes, high latency, instance restarts. | High |
| P9.4 | Set up log aggregation | Configure Cloud Logging sinks. Export logs to BigQuery or external service for long-term retention. | Medium |
| P9.5 | Document API response formats | Separate API reference document showing example responses for each endpoint. Useful for downstream consumers. | Medium |
| P9.6 | Add rate limiting protection | Implement request rate limiter to protect against abuse. Allow reasonable burst (e.g., 100 req/min per IP). | Low |
| P9.7 | Add health check endpoint for Cloud Run | Ensure `/health` responds within 500ms. Cloud Run uses this for instance liveness probes. | Medium |
| P9.8 | Optional: Add VOD replay support | If B.LEAGUE provides separate replay/VOD endpoints beyond `missed_urls`, add dedicated replay resolution flow. | Low |
| P9.9 | Optional: Multi-quality selector | Add `?quality=` query parameter to `/stream/{game_id}` to let clients choose Full HD / High / Middle / Low instead of hardcoded Middle. | Low |
| P9.10 | Optional: WebSocket updates | Real-time phase changes for live games via WebSocket. Push phase update from Playing → Finished when game ends. | Low |

### Deliverable
Production-ready service with monitoring, documentation, and optional enhancements implemented. Ready for ongoing operation.

### Acceptance Criteria
- Service passes all automated tests
- Monitoring dashboards show healthy metrics
- README clearly explains how to self-host
- API reference documents help third-party integrators
- Incident response procedures documented

---

## 11. Post-Implementation Repository Cleanup

After all development phases complete, perform a final repository cleanup before the first public commit:

### Goal
A clean repo with **zero residue** from nbl1-streams and zero decompiled APK artifacts.

### Must remove
- `sources/ninenow/`, `sources/sbs/`, `sources/plex/`, `sources/samsung/`, `sources/auftv/` — upstream sources not part of bleague-streams
- `sources/nbl1/` if copied over as scaffolding — must be completely replaced or removed
- Entire `decompiled/` directory containing `.class` files, Android resource XML dumps, JADX output, or any reverse-engineered binary artifacts
- `test_seasons.py`, `CLAUDE.md` (upstream test harnesses / AI instructions)
- `specs/7plus.md`, `specs/spec.md` (unrelated upstream specs)
- Any `*.apk`, `*.xapk`, `index.html`, local dumps

### Must keep
- `main.py` — adapted FastAPI app for bleague only
- `spec.md` — full B.LEAGUE specification
- `plan.md` — this implementation plan
- `SCHEDULE.md` — Basket LIVE API reference (critical for understanding encryption/license key flow)
- `Dockerfile`, `requirements.txt`, `deploy.sh`
- `README.md` — project overview and deployment guide
- `CHANGELOG.md`
- `sources/bleague/` — the only source module
- `.gitignore`

### Verification checklist

| Check | Pass |
|-------|------|
| No `decompiled/` directory exists | ☐ |
| No APK-related files (*.apk, *.xapk, index.html) exist | ☐ |
| Only `sources/bleague/` under sources/ | ☐ |
| spec.md, plan.md, SCHEDULE.md present in root | ☐ |
| main.py imports only bleague, no other source modules | ☐ |
| deploy.sh has zero --set-env-vars flags | ☐ |
| Repo size < 1 MB excluding .git/ | ☐ |

---

## 12. Parallelisation Opportunities

Several phases can execute in parallel, reducing total calendar time:

| Parallel Group | Phases | Notes |
|---------------|--------|-------|
| **Group A** | P4 (JSON API) + P5 (M3U) + P6 (EPG) | All consume same game data from P2. Can develop simultaneously if interfaces agreed. |
| **Group B** | P7 (Deployment) + P8 (Testing) | Deployment scripts written while integration testing runs. Both depend on core functionality being complete. |
| **Across entire timeline** | P9 (Polish) items P9.3/P9.4 | Monitoring configuration runs alongside everything else once service is deployable. |

Maximum parallelism: 4 phases running simultaneously (P4, P5, P6, P7) during weeks 3–4.

---

## 13. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| API returns 403 (geo-block) | Entire service broken | Deploy exclusively to `asia-northeast1`. Add explicit geo-availability check in health probe. |
| License key rotates | All streams break until updated | FreeKEY is hardcoded in source. Rotation requires a source change + redeploy (simple git push → automated rebuild). Monitor segment decryption failures nightly to detect rotation early. |
| Akamai CDN changes encryption | Decryption fails silently | Automated nightly test against a known-good game ID. Alert if decrypted segment doesn't yield valid TS sync byte. |
| High traffic overwhelms upstream | Rate-limiting errors cascade | Semaphore limits concurrency. Retry with backoff. Consider caching game-detail responses longer during peak. |
| Cold start latency | First request slow (>5s) | Maintain `--min-instances 1`. Pre-warm with periodic ping if truly cold starts become problematic. |
| Firestore dependency unavailable | Fallback registry broken | Make Firestore optional. Core functionality works without it (in-memory map suffices for single-instance deployments). |

---

## 14. File Structure (Target State)

```
bleague-streams/
├── main.py                     # FastAPI app: lifespan, all endpoints
├── Dockerfile
├── requirements.txt
├── deploy.sh
├── README.md
├── CHANGELOG.md
├── spec.md                     # This document references spec.md
├── sources/
│   ├── __init__.py
│   └── bleague/
│       ├── __init__.py         # Constants, HTTP client, fetch functions, phase computation, enrichment
│       └── tests/              # Module-level unit tests
├── tests/
│   ├── conftest.py             # Shared fixtures, mock HTTP responses
│   ├── test_live.py            # Live/upcoming game identification trace (mirrors nbl1-test)
│   ├── test_teams.py           # Team catalog index verification
│   ├── test_playkey.py         # License key exchange verification
│   ├── test_proxy.py           # Playkey endpoint, playlist rewrite, segment forwarding
│   └── test_epg.py             # XMLTV schema validation, channel ID correlation
└── .github/
    └── workflows/
        └── ci.yml              # Automated test pipeline on push
```

---

## 15. Success Criteria

When all phases are complete and accepted:

1. **Live games visible**: Anyone accessing `/games/live` sees current B.LEAGUE men's games with accurate phases and playable stream redirects.
2. **Encrypted streams playable**: Third-party players connecting to `/stream/{game_id}` and receiving the playlist can successfully decrypt and watch HLS segments.
3. **Catch-up available**: Recent completed games with catch-up URLs are listed in `/games/completed` and the M3U playlist.
4. **EPG functional**: IPTV players consuming `/pl/epg.xml` see BLeague programmes with correct timing descriptions.
5. **Geo-resident**: All API and CDN requests originate from Japan, avoiding the 403 block.
6. **Resilient**: Transient upstream failures are handled with retry/backoff. Non-critical failures (single game missing stream) don't collapse the whole response.
7. **Observable**: Monitoring dashboards show live system health. Anomalous events trigger alerts.
8. **Self-hostable**: Any developer can clone, configure the freekey credential, deploy to Cloud Run, and have a working service in under an hour.
