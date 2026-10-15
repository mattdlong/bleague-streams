# Bleague Streams — Progress Report

**Date:** 2026-10-15  
**Status:** ✅ Complete — 8/8 tests passing, all endpoints verified  
**Service URL:** `https://bleague-streams-191043172686.asia-northeast1.run.app`  
**Project:** `virtual-machines-437208` / `asia-northeast1`

---

## Goal

Build a standalone FastAPI service that aggregates **B.LEAGUE men's basketball** schedules, stream URLs, and program guide data into three consumable formats:
1. **JSON API** (`/games/live`, `/games/completed`)
2. **M3U Playlist** (`/pl/`)
3. **XMLTV EPG** (`/pl/epg.xml`)

Mirrors the architecture of [nbl1-streams](https://github.com/mattdlong/nbl1-streams) but adapted to B.LEAGUE's encrypted HLS (AES-128) model and Japanese-language API.

---

## What We Built

| Endpoint | Response | Status |
|----------|---------|--------|
| `/health` | `{"status": "ok"}` | ✅ |
| `/health/diagnostic` | Full pipeline trace with catalog enrichment | ✅ |
| `/games/live` | 248 B.LEAGUE men's games (119 with stream URLs) | ✅ |
| `/games/completed` | 32 completed games (all with catch-up videos) | ✅ |
| `/pl/` | 280 M3U entries grouped by league + phase | ✅ |
| `/pl/epg.xml` | 280 channels, 531 programme slots | ✅ |
| `/stream/{match_id}` | 302 redirect to Akamai HLS master playlist | ✅ |
| `/playkey` | 16-byte AES-128 license key | ✅ |

---

## Implementation Details

### Source layers

- **`main.py`** — FastAPI app with lifespan, 8 endpoints, playlist helpers, EPG builder
- **`sources/bleague/__init__.py`** — Core business logic (~630 lines):
  - Hardcoded auth headers & FREEKEY credential
  - `fetch_day_schedule()` / `fetch_month_schedule()` — SoftBank API calls
  - `fetch_team_catalog()` — Builds index of 55 B.LEAGUE teams (string-normalized IDs)
  - `compute_phase()` — 7-case clock evaluation per SCHEDULE.md (JST-aware)
  - `resolve_stream_for_match()` — Game detail → quality selection → CDN URL
  - `enrich_game()` — Builds canonical game dict with team names, scores, venues

### Data pipeline

```
Fetch day + month schedules
    ↓ filter for B.LEAGUE ONE/PREMIER/NEXT
Compute phase (game_end_f + timestamps)
    ↓ classify as LIVE or COMPLETED
Enrich with team catalog (JP + EN names)
    ↓ resolve stream URLs via game detail
Emit JSON API + M3U playlist + XMLTV EPG
```

### Encryption handling

B.LEAGUE uses HLS AES-128 segment encryption via SoftBank's `/api/v1/playkey` license server. The SDK player attaches a recursive header (`PlayerConfiguration.KEY_DRM_AES_SERVER_HEADER`) that injects the FREEKEY credential. Third-party players (VLC, mpv) can't do this, so the service proxies the key exchange:

- Media playlists have `#EXT-X-KEY: URI="https://basketball.mb.softbank.jp/api/v1/playkey"` rewritten to point at the service host's `/playkey` endpoint
- `/playkey` returns the 16-byte AES-128 key obtained via the FREEKEY credential
- A catch-all route (`/{path:path}`) forwards CDN playlists and segments, rewriting KEY URIs on the fly

---

## Bugs Fixed

| Bug | Root cause | Fix |
|-----|-----------|-----|
| `TypeError: can't compare offset-naive and offset-aware datetimes` | `parse_ts()` returned naive datetime objects but `now_jst` was JST-aware | Attach `tzinfo=JST` to all parsed timestamps in `compute_phase()` |
| `LocalProtocolError: Illegal header value b'- '` | httpx rejects `- ` (dash + space) in `X-BASKETLIVE-ACCESSTOKEN` per RFC 7230 | Normalize to single `-` without trailing space |
| Team catalog filter no-op | Condition used `pass` block with broken logic | Replaced with explicit `valid_league_substrings` matching |
| Team enrichment returns `KEY NOT IN CATALOG` | Catalog returns `team_id` as **int**, games reference as **str** | Normalize to `str(tid)` when building index keys |

---

## Architecture Decisions

- **httpx over aiohttp**: Async-native, strict RFC compliance, matches NBL1 project pattern
- **No env vars**: All credentials hardcoded (matches NBL1's `Authorization: Bearer xxx.app` compile-time constant pattern)
- **Single source module**: Only `sources/bleague/__init__.py` and `proxy.py` — no multi-source aggregation needed
- **Cloud Run `asia-northeast1`**: Required for SoftBank API access (geo-block returns 403 from outside Japan)
- **No browser dependency**: Explicitly removed Playwright Chromium from Dockerfile (only needed for SBS source in upstream project)

---

## Known Limitations

- **Geo-block outside Japan**: API calls fail with 403 when originating from non-Japanese IPs
- **Fixed 2-hour EPG slots**: Programme duration hardcoded, doesn't reflect actual game length
- **No live score tracking**: Schedule rows don't carry scores mid-game
- **Single quality default (Middle)**: No adaptive bitrate switching; stream URL for other qualities requires re-fetching game detail with different quality filter
- **Licence key is static**: Same 16-byte key for all games/channels; if SoftBank rotates this credential, a source change + redeploy is needed

---

## Final Test Results

```
✓ TEST 1  /health
✓ TEST 2  /health/diagnostic — raw_day=4, teams=55, live_filter=248
✓ TEST 3  /games/live     — count=248, with_stream=119, home_en=CHIBAJETS
✓ TEST 4  /games/completed — count=32, with_videos=32
✓ TEST 5  /pl/             — M3U playlist with 280 entries
✓ TEST 6  /pl/epg.xml      — 280 channels, 531 programmes
✓ TEST 7  /stream/507231   — redirects to HLS master playlist
✓ TEST 8  /playkey          — returns 16-byte AES key
────────────────────────────────────────────────────────────
RESULTS: 8/8 tests passed
Service is production-ready
```

All endpoints verified against live container in `asia-northeast1`.