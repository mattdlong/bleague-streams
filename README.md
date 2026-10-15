# B.LEAGUE Streams Service

FastAPI service that aggregates B.LEEGUE (men's) basketball schedules, encrypted HLS live/catch-up streams, and electronic program guide into three consumable outputs: JSON APIs, an IPTV-compatible M3U playlist, and XMLTV EPG.

**FORK of** [mattdlong/nbl1-streams](https://github.com/mattdlong/nbl1-streams) — shares the same architectural pattern (modular sources, shared endpoint infrastructure, Docker → Cloud Run). Adapted for B.LEEGUE's different API structure and AES-128 encryption model.

## Quick Start

### Local Development

```bash
cd bleague-streams
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

All endpoints respond immediately. Game data returns empty when running outside Japan due to the SoftBank load balancer geo-block (`403`) — this is expected. Deploy to the Cloud Run instance in `asia-northeast1` to get real game data.

### Deployment

```bash
./deploy.sh
```

Deploys to Google Cloud Run region `asia-northeast1` (Japan/Tokyo). No environment variables needed — all credentials are compiled directly into the container image.

## Architecture

```
┌──────────────────┐
│   FastAPI App     │  /health, /games/live, /games/completed,
│                   │  /stream/{id}, /pl/, /pl/epg.xml,
│                   │  /playkey, /*  (CDN forwarder)
└──┬───────┬───────┘
   │       │
   ▼       ▼
┌──────────┴──────────┐
│  sources/bleague    │  Core logic module
│  ┌────────────────┐ │
│  │ schedule        │ │  Fetch & parse day/month schedule
│  │ + team catalog  │ │  Resolve English club names
│  │ + phase compute │ │  Compute game phase from flags/timestamps
│  │ + stream resolv │ │  Resolve CDN URLs via game detail endpoint
│  └────────────────┘ │
│                     │
│  ┌────────────────┐ │
│  │ proxy.py       │ │  /playkey → inject FREEKEY credential →
│  │                │ │    return 16-byte AES-128 key
│  │  CDN forwarder │ │  Rewrite #EXT-X-KEY URIs from softbank →
│  │  + KEY rewrite │ │    localhost/playkey for player decryption
│  └────────────────┘ │
└─────────────────────┘
```

### Data Sources

All upstream calls use identical auth headers (`User-Agent: okhttp/4.9.3`, blank `X-BASKETLIVE-ACCESSTOKEN`). The schedule and team-catalog endpoints require no stored session token. A `401` response is the only status code triggering retry in the native app.

| Endpoint | Purpose | Auth Required |
|----------|---------|---------------|
| `/api/v5/schedules?league_id=ALL&game_date={yyyyMMdd}` | Day schedule | Blank token sufficient |
| `/api/v5/schedules/month?league_id=ALL&game_month={yyyyMM}` | Month schedule | Blank token sufficient |
| `/api/v3/teams` | Team catalog (English names) | Blank token sufficient |
| `/api/v9/game/{game_id}` | Game detail (stream URLs) | Blank token sufficient |
| `/api/v1/playkey` | AES-12 license key exchange | `X-BASKETLIVE-FREEKEY` header required |

The license server at `basketball.mb.softbank.jp/api/v1/playkey` rejects requests without the FREEKEY credential with `{"message":"forbidden"}`. The hardcoded FREEKEY value is compiled into `sources/bleague/__init__.py`. See `decompiled/sources/jp/co/bleague/data/L0.java` line 559 for the origin reference.

### Geo-Restriction

Calls from outside Japan to `api.basketball.mb.softbank.jp` receive a `403` accompanied by an HTML page from the load balancer. Calls originating from within Japan reach the backend API normally. **Deployment must be in `asia-northeast1`**. The Akamai CDN segments also fail with 403 when fetched cross-border.

## Endpoints

| Path | Method | Response Type | Description |
|------|--------|---------------|-------------|
| `/health` | GET | `{"status": "ok"}` | Health check (response <500ms) |
| `/games/live` | GET | JSON | Upcoming and in-progress games for today and tomorrow (JST); stream URL set while playing |
| `/games/completed` | GET | JSON | Games finished in the last 7 days (JST) with catch-up URLs |
| `/stream/{match_id}` | GET | 302 redirect or 404 | Resolves the CDN master URL and redirects to the same path on this service's CDN proxy; 404 if no stream available |
| `/pl/` | GET | M3U text | IPTV-compatible playlist with grouped channels |
| `/pl/epg.xml` | GET | XMLTV XML | Electronic program guide with channel programmes |
| `/playkey` | GET | 16 bytes binary | AES-128 decryption key (injects FREEKEY on upstream call) |
| `/ch{NNN}/...` | GET | CDN forwarded | Forwards to Akamai CDN; rewrites `#EXT-X-KEY` URIs to `/playkey`. Any other path is 404 |

### Output Formats

**JSON (`/games/live`, `/games/completed`):**

```json
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
        "round": null,
        "fixture_type": "",
        "season_name": "B.LEAGUE ONE",
        "venue": "盛岡タカヤアリーナ",
        "conference": "B.LEAGUE ONE",
        "home_team": {
            "id": "709",
            "name_jp": "岩手ビッグブルズ",
            "name_en": "IWATE BIGBULLS",
            "name_es": "IWATE",
            "score": null,
            "logo_url": null
        },
        "away_team": { ... },
        "live_urls": [...],
        "missed_urls": [],
        "stream_url": "https://...akamaized.net/..."
    }]
}
```

**M3U Playlist (`/pl/`):**

Games are grouped into `{League} Live`, `{League} Upcoming`, and `{League} Completed`. Every entry points at `/stream/{id}`, so playback always runs through the key-rewriting CDN proxy. Labels use English club names from the team catalog (plus a small built-in table for overseas opponents not in the catalog); rows with no teams (TV shows, tour games) use the row's `title`.

**XMLTV EPG (`/pl/epg.xml`):**

Each upcoming/live game gets its own `<channel>` element with up to three programme slots (Upcoming → Live window → Completed window), clipped to the two-day guide window. Completed games are on demand, so each gets one programme spanning the whole window. Programme duration is fixed at 2 hours regardless of actual game length. Times are converted from JST to UTC for XMLTV compliance.

## How It Works

### Phase Computation

Game status is computed from `game_end_f` (server flag) combined with three timestamp fields (`live_start_date`, `live_end_date`, `missed_live_end_date`) using GMT+9 equivalent clock evaluation:

| Condition | Phase | Category |
|-----------|-------|----------|
| `flag == "99"` | `CANCELLED` | Excluded from output |
| `end < now < missedEnd` | `FINISHED_MISSED_MATCH` | Completed group (catch-up available) |
| `now < day_midnight` | `IN_FUTURE` | Upcoming group |
| `day < now < start` | `IN_FUTURE_ONE_DAY` | Upcoming group |
| `start < now < end` | `PLAYING` | Live group |
| Otherwise | `FINISHED` | Completed group |

Missing timestamps fall back to flag-only mapping. Clock windows are open-intervals — boundary values do NOT qualify.

### Stream Resolution

Games resolve streams through the game-detail endpoint:

1. Call `/api/v9/game/{game_id}` → returns `live_urls[]` and `missed_urls[]`
2. Select appropriate list based on computed phase (per SCHEDULE.md Section 5.2)
3. Pick "Middle" quality (default preset)
4. Redirect (302) to the same CDN path on this service, e.g. `/ch914/ndvr_MAN02.m3u8`. The playlists use relative URIs, so variant playlists and segments then also come through the proxy

Game detail is cached for 60s (the CDN URLs carry no expiring tokens), and listing endpoints fetch details concurrently.

### AES-128 Decryption Proxy

Streams use HLS AES-12 segment encryption where each segment has its own IV tag. The decryption key is obtained from the SoftBank playkey endpoint requiring the FREEKEY credential. Third-party players (VLC, mpv, TiviMate) cannot attach custom headers to license-key requests.

The proxy bridges this gap:

1. Media playlists served through `/{path}` have their `#EXT-X-KEY` URI rewritten from `https://basketball.mb.softbank.jp/api/v1/playkey` → `http://{service-host}/playkey`
2. The `/playkey` endpoint injects the FREEKEY header and forwards to SoftBank
3. Returns raw 16-byte AES-128 key to the player
4. Player decrypts segments locally

### Quality Presets

| Code | Display Label | Resolution | Bandwidth |
|------|--------------|------------|-----------|
| `8M` | Full HD | 1920×1080 | ~6.1 Mbps |
| `4M` | High | 1280×720 | ~2.1 Mbps |
| `1.5M` | Middle | 854×480 | ~1.1 Mbps |
| `500K` | Low | 320×180 | ~628 Kbps |

Default preset: Middle (854×480, ~1 Mbps). Configurable via env var `BLEAGUE_QUALITY`.

## File Structure

```
bleague-streams/
├── main.py                     # FastAPI app: lifespan, all endpoints
├── Dockerfile                  # python:3.12-slim, uvicorn
├── deploy.sh                   # gcloud run deploy (asia-northeast1)
├── requirements.txt            # fastapi, httpx, cachetools, pycryptodome, firestore
├── README.md                   # This file
├── CHANGELOG.md                # Version history
├── spec.md                     # Full specification document
├── plan.md                     # Implementation plan
├── SCHEDULE.md                 # Basket LIVE reverse-engineering reference
└── sources/
    ├── __init__.py             # Empty package marker
    └── bleague/
        ├── __init__.py         # Constants, fetch functions, phase computation, enrichment
        └── proxy.py            # Playkey endpoint, CDN forwarder, KEY URI rewriting
```

## Known Gaps

- Cancelled/abandoned games are silently excluded from both live and completed endpoints (same pattern as nbl1-streams). The filtering set does not explicitly include these statuses.
- English name availability varies by league type — W-League teams are not in the `/api/v3/teams` catalog. B.LEEGUE men-only is enforced during catalog parsing.
- Fixed 2-hour programme duration in EPG regardless of actual game length.
- Venue names are Japanese only; the API has no English venue field for these rows.
- Some players (e.g. ffmpeg) reject the extensionless segment names by default; ffmpeg needs `-extension_picky 0`.
- Licence key is hardcoded in source. Rotation requires source change + redeploy.

## Dependencies

Built following the same patterns as [nbl1-streams](https://github.com/mattdlong/nbl1-streams): TTLCache for short-lived in-process caching, httpx.AsyncClient with semaphore throttling, asyncio.gather for parallel conference fetching, and modular source architecture.

## License

This project adapts work from the upstream nbl1-streams repository under its original license.
