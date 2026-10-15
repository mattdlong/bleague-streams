# B.LEAGUE Streams Service Specification

Service name: `bleague-streams`
Repository target: `https://github.com/mattdlong/bleague-streams` (FORK of `mattdlong/nbl1-streams`)
Deployment: Google Cloud Run — region `asia-northeast1` (Japan)

This service aggregates B.LEAGUE (men only) basketball match schedules, encrypted HLS live/catch-up streams, and program listings into three outputs:

1. **JSON APIs** — today's live/upcoming games and recently completed games with phase status and stream metadata
2. **M3U playlist** (`/pl/`) — IPTV-compatible channel list with embedded stream redirects and proxy endpoints
3. **XMLTV EPG** (`/pl/epg.xml`) — electronic program guide consumed by IPTV players

The service follows the same architectural pattern as the upstream `nbl1-streams` project: FastAPI application with modular sources under `sources/<name>/`, shared endpoint infrastructure in `main.py`, Docker containerization, and Cloud Run deployment. The key difference is that B.LEAGUE streams use **HLS AES-128 segment encryption** behind a SoftBank license server rather than NBL1's plain signed-Akamai manifests.

---

## Table of Contents

1. [Project Layout](#1-project-layout)
2. [Upstream API Reference](#2-upstream-api-reference)
3. [Game Status Computation](#3-game-status-computation)
4. [English Team Names](#4-english-team-names)
5. [Stream Resolution & Quality Selection](#5-stream-resolution--quality-selection)
6. [AES-128 Encryption & License Key Exchange](#6-aes-128-encryption--license-key-exchange)
7. [Business Logic: Live/Upcoming Games](#7-business-logic-liveupcoming-games)
8. [Business Logic: Completed Games](#8-business-logic-completed-games)
9. [Stream Proxy & Playkey Endpoint](#9-stream-proxy--playkey-endpoint)
10. [Playlist Generation (NBL1 Portion)](#10-playlist-generation-bleague-portion)
11. [EPG Generation (BLeague Portion)](#11-epg-generation-bleague-portion)
12. [Concurrency Model](#12-concurrency-model)
13. [Caching Strategy](#13-caching-strategy)
14. [Error Handling](#14-error-handling)
15. [Constants & Configuration](#15-constants--configuration)
16. [Known Gaps & Edge Cases](#16-known-gaps--edge-cases)
17. [Containerization & Deployment](#17-containerization--deployment)
18. [Testing Infrastructure](#18-testing-infrastructure)
19. [Endpoint Summary](#19-endpoint-summary)

---

## 1. Project Layout

```
bleague-streams/
├── main.py                     # FastAPI app: lifespan, endpoints, playlist & EPG generators
├── Dockerfile                  # python:3.12-slim, playwright chromium (optional), uvicorn
├── deploy.sh                   # gcloud run deploy script
├── requirements.txt            # fastapi, httpx, cachetools, google-cloud-firestore
├── spec.md                     # This specification document
├── sources/
│   └── bleague/__init__.py     # B.LEAGUE source: schedule, teams, games, streams
├── test_schedule.py            # Test harness tracing game date identification
└── README.md
```

Each source module implements the shared interface: `startup()`, `shutdown()`, `fetch_live_games()`, `fetch_completed_games()`. The main app delegates to these functions and renders output into JSON, M3U, and XMLTV formats.

---

## 2. Upstream API Reference

All Basket LIVE API calls share identical authentication headers. No stored session token is required — a blank access token is sufficient for all schedule and game detail calls.

### Authentication Headers

| Header | Value |
|--------|-------|
| `User-Agent` | `okhttp/4.9.3` |
| `X-OS-TYPE` | `ANDROID` |
| `X-APP-VERSION` | `4.0.2` |
| `X-BASKETLIVE-ACCESSTOKEN` | `- ` (blank when no session token stored) |

The blank token header value is exactly `- ` (dash followed by a single space). Schedule and game-detail calls return `200` with this header. A `401` response is the only status for which the native app refreshes the session and retries.

### Geographic Restriction

Calls from outside Japan to the load balancer at `api.basketball.mb.softbank.jp` receive a `403` response accompanied by an HTML page. Calls originating from within Japan reach the backend API normally. The service **must** be deployed to a Japan-region Cloud Run instance (`asia-northeast1`).

### Language Behavior

The API returns Japanese names exclusively. There is no `lang` parameter, no `Accept-Language` negotiation, and no translated copy on a secondary host. Parameters `lang=en` and `Accept-Language: en` have zero effect on response content. English names appear only as separate `_e` and `_es` fields on specific endpoints (Section 4).

### 2.1 Day Schedule Endpoint

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id={id}&game_date={date}
```

Query parameters:

| Parameter | Format | Required | Notes |
|-----------|--------|----------|-------|
| `league_id` | Numeric or `ALL` / `BLEAGUE` | Yes | Must be exact case. `All` is rejected. `All` triggers 400: 「値が不正です。数値、または[ALL, BLEAGUE]のみ許可されています」 |
| `team_id` | Numeric | No | Sent only while league filter is B.LEAGUE or league id is empty |
| `game_date` | `yyyyMMdd` | Yes | Calendar date, e.g. `20261014` |

Example:

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261014
```

Response: `200`, `Content-Type: application/json`, body ~79 KB. Response header `Last-Modified: 2026-10-14 07:40:44 GMT`.

Top-level keys: `games` (array of game rows) and `schedule` (array of date strings in `yyyyMMdd` format spanning historical dates).

#### Game Row Fields

| JSON Field | Type | Role |
|------------|------|------|
| `game_id` | string | Unique game identity (integer encoded as string, e.g. `"507231"`) |
| `game_date` | string | Calendar date `yyyy-MM-dd` |
| `game_time` | string | Tip time `HH:mm:ss` |
| `game_end_f` | string | Server phase flag (see Section 3) |
| `live_start_date` | string | Live window start, `yyyy-MM-dd HH:mm:ss` |
| `live_end_date` | string | Live window end |
| `missed_live_end_date` | string | Catch-up window end |
| `home_team_id` | string | Join key to team catalog |
| `away_team_id` | string | Join key to team catalog |
| `home_team_name` | string | Japanese club name (home) |
| `away_team_name` | string | Japanese club name (away) |
| `home_team_name_s` | string | Japanese short name (home) |
| `away_team_name_s` | string | Japanese short name (away) |
| `stadium_name` | string | Japanese venue name |
| `league_name` | string | Japanese league label (e.g. "B.LEAGUE ONE") |
| `schedule_class_name` | string | Japanese class label |
| `schedule_class_nameE` | string | English class label (often null) |
| `league_image_url` | string | League image URI (only URL-like field on schedule row) |

Other fields exist but are not used for phase/status/stream determination: `boost_flg`, `channel`, `commentary_person`, `delivery_bit_rate`, `free_flg`, `is_multiple`, `multiple`, `tracking`, etc.

#### Month Schedule Endpoint

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules/month?league_id={id}&game_month={month}
```

Same auth headers. Query:

| Parameter | Format | Required |
|-----------|--------|----------|
| `league_id` | Numeric or `ALL` / `BLEAGUE` | Yes |
| `team_id` | Numeric | No |
| `game_month` | `yyyyMM` | Yes |

Example:

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules/month?league_id=ALL&game_month=202610
```

Response: Same top-level keys `games` and `schedule`. Each entry in `games` is a date bucket: `{ date, date_order, games[] }`. The inner `games` array contains the same game-row objects as the day call.

### 2.2 Team Catalog Endpoint

```
GET https://api.basketball.mb.softbank.jp/api/v3/teams
```

No query parameters. Same auth headers. Returns team metadata keyed by league type. Body key: `teams_info` — array of blocks, each containing `team_kind_id`, `team_kind_name`, `team_kind_name_s`, and `items`. Each item is a conference with `team_conference_cd`, `team_conference_name`, and a `teams` array of club objects.

| JSON Field | Example (レバンガ北海道) | Role |
|------------|-------------------------|------|
| `team_id` | `"702"` | Matches `home_team_id` or `away_team_id` from schedule rows |
| `team_name` | レバンガ北海道 | Japanese full name |
| `team_name_s` | 北海道 | Japanese short name |
| `team_name_3s` | レバン | Three-character abbreviation |
| `team_name_e` | LEVANGA HOKKAIDO | Full English name |
| `team_name_es` | HOKKAIDO | Short English name |

### 2.3 Game Detail Endpoint

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/{game_id}
```

Same auth headers. Body key: `game`. Contains the game object enriched with stream addresses.

| JSON Field | Meaning |
|------------|---------|
| `home_team_name_e` | Home club, full English (often absent on wire) |
| `home_team_name_es` | Home club, short English (often absent) |
| `away_team_name_e` | Away club, full English (often absent) |
| `away_team_name_es` | Away club, short English (often absent) |
| `stadium_name_e` | Venue, English (often absent) |
| `schedule_class_nameE` | Class label, English (often null) |
| `live_urls` | Array — see Section 5 |
| `missed_urls` | Array — see Section 5 |
| `game_end_f` | Server phase flag (same values as schedule row) |

**Critical note:** English fields (`*_e`, `*_es`) are frequently absent on the wire. A call for game `110080` returned `200` with Japanese names only; those fields were completely missing from the payload. The team catalog endpoint is the reliable source for English names. Do not assume game detail populates them.

Other methods logged as `getGameDetail` still route to this v9 endpoint, NOT to the v3 `/game/detail` path (which returns a different format: `past_match`, `video`, `videos`, team stats — irrelevant for stream listing).

### 2.4 Additional API Endpoints (Reference Only)

These endpoints expose the same game data fields as the schedule/game calls but serve different screen contexts in the app. The service may reference them for additional context:

| Endpoint | Context |
|----------|---------|
| `/api/v5/top/live?devices=&team_id=&league_id=` | Live screen |
| `/api/v6/live?league_id=` | Live view |
| `/api/v6/missed?league_id=&offset=` | Missed/catch-up view |
| `/api/v3/game/detail?game_id=&game_date=&missed=` | Past-match view (different response shape) |

Fields `live_start_date`, `live_end_date`, `missed_live_end_date`, and `game_end_f` appear consistently across all these payloads. The schedule tab does not poll — it reloads on screen creation, visibility transition, manual refresh, or league change.

---

## 3. Game Status Computation

The server does not return a computed status enum. The client (and this service) computes one from `game_end_f` combined with three timestamp fields. All six computed phases map to categories used throughout the pipeline.

### 3.1 The Flag

`game_end_f` is always a string:

| Value | Name | Meaning |
|-------|------|---------|
| `"9"` | before | Not started |
| `"0"` | during | In progress |
| `"1"` | over | Ended |
| `"99"` | cancel | Cancelled |

Any other value falls through to the clock rules (see below). Missing timestamps parse as epoch, making fall-through effectively unusable for non-standard flags.

### 3.2 Missing Timestamps Fallback

If any of `live_start_date`, `live_end_date`, or `missed_live_end_date` is null or blank, the flag maps directly:

| `game_end_f` | Phase |
|--------------|-------|
| `"9"` | Future |
| `"0"` | Playing |
| `"1"` | Finished |
| `"99"` | Cancelled |

### 3.3 Clock-Based Status (Primary Rule)

When all three timestamps exist, compare a clock shifted to GMT+9 against the windows:

The client shifts "now" by the difference between the device zone and GMT+9. Timestamps are parsed in the device's default zone without applying that same shift back. Windows are open-interval: an instant exactly on a boundary is **not** inside the window.

Define:
- `now`: shifted real-time clock (GMT+9 equivalent)
- `day`: midnight (00:00:00) of the `live_start_date` calendar day
- `start`: `live_start_date`
- `end`: `live_end_date`
- `missedEnd`: `missed_live_end_date`

Evaluation order (short-circuit):

1. **Cancelled**: If `game_end_f == "99"` → phase = `CANCELLED`
2. **Missed/Finished**: If `game_end_f == "1"`:
   - `end < now < missedEnd` → phase = `FINISHED_MISSED_MATCH`
   - Otherwise → phase = `FINISHED`
3. **Future**: If `now < day` → phase = `IN_FUTURE`
4. **Not-yet-live (day-of)**: If `day < now < start` → phase = `IN_FUTURE_ONE_DAY` (shows one-second countdown UI)
5. **Playing**: If `start < now < end` → phase = `PLAYING`
6. **Missed**: If `end < now < missedEnd` → phase = `FINISHED_MISSED_MATCH`
7. **Finished**: Otherwise → phase = `FINISHED`

Flags `"9"` and `"0"` do **not** override the clock once the three timestamps exist. A server flag of `"during"` will still display as future while `now < start`. This means the clock rule takes precedence over the flag for timestamps-present cases.

### 3.4 Phase-to-Category Mapping

Six computed phases map to operational categories used by the service:

| Phase | Category | Description |
|-------|----------|-------------|
| `IN_FUTURE` | Upcoming | Game not yet on the calendar day |
| `IN_FUTURE_ONE_DAY` | Upcoming (Today) | On the calendar day but before live start |
| `PLAYING` | Live | Within live window |
| `FINISHED_MISSED_MATCH` | Live (Catch-up) | Live window passed but catch-up available |
| `FINISHED` | Completed | All windows passed |
| `CANCELLED` | Cancelled | Game cancelled |

For `/games/live`, categories `Live`, `Live (Catch-up)`, and `Upcoming` qualify. For `/games/completed`, category `Completed` qualifies. `Cancelled` is excluded from both.

---

## 4. English Team Names

There is no second host, no translation endpoint, and no language parameter that affects responses. English names appear only as supplementary fields on specific endpoints, often absent.

### 4.1 Where English Names Exist

| Endpoint | English Fields | Availability |
|----------|---------------|--------------|
| `/api/v3/teams` | `team_name_e`, `team_name_es` | Populated (reliable) |
| `/api/v5/schedules` | `schedule_class_nameE` | Often null, sometimes has trailing whitespace on wire |
| `/api/v9/game/{id}` | `home_team_name_e`, `away_team_name_e`, `stadium_name_e`, `schedule_class_nameE` | Frequently absent |

On 14 October 2026, game `110080` returned `200` via `/api/v9/game/{id}` with **Japanese names only**. The `*_e` fields were completely absent from the payload. English names cannot be assumed present on schedule or game-detail rows.

### 4.2 Team Catalog Lookup Process

To resolve English names for a match:

1. Fetch `/api/v3/teams` (once per scheduling cycle, cached long-term)
2. Build an index mapping `team_id` → `(team_name_e, team_name_es)` across all conferences
3. When building game dicts, look up `home_team_id` and `away_team_id` against this index
4. Populate `home_team_name_e`, `away_team_name_e`, `home_team_name_es`, `away_team_name_es` fields

Team catalog is structured:
```
teams_info[n].items[c].teams[m]
```
Where:
- `teams_info[n]`: block for league type (by `team_kind_id`)
- `items[c]`: conference within that league type
- `teams[m]`: individual club objects

Teams are identified by numeric `team_id`. Match against schedule rows' `home_team_id` / `away_team_id` (string-encoded integers).

### 4.3 Scope Limitation

Only B.LEAGUE teams (team_kind_id values corresponding to B.LEAGUE PREMIER, B.LEAGUE ONE, B.LEAGUE NEXT) are included in scope. Wリーグ teams are excluded. The `/api/v3/teams` catalog includes teams across all types — the service must filter by the relevant `team_kind_id` values during catalog fetch.

---

## 5. Stream Resolution & Quality Selection

Stream URLs are obtained from the game detail endpoint, not the schedule endpoint. The schedule row carries no URL-like fields (except `league_image_url`). The game detail endpoint provides two lists: `live_urls` and `missed_urls`.

### 5.1 Game Detail Stream Lists

```json
{
  "game": {
    "live_urls": [
      { "quality": "Full HD",  "url": "https://..." },
      { "quality": "High",     "url": "https://..." },
      { "quality": "Middle",   "url": "https://..." },
      { "quality": "Low",      "url": "https://..." }
    ],
    "missed_urls": [
      { "quality": "Full HD",  "url": "https://..." },
      ...
    ]
  }
}
```

| List | Content | Usage |
|------|---------|-------|
| `live_urls` | Four entries per stream (Full HD, High, Middle, Low) | Live playback, catch-up fallback |
| `missed_urls` | Same four qualities | Catch-up playback after live window |

Both lists use the same CDN host pattern: `basketlive-live-sni.akamaized.net/live/ch{NNN}/...`. The channel identifier `ch{NNN}` varies per game/match configuration.

Quality labels are display-name strings that must be matched exactly — they are NOT codes. The matching table is:

| Preset Code | Display Label |
|-------------|---------------|
| `8M` | `Full HD` |
| `4M` | `High` |
| `1.5M` | `Middle` |
| `500K` | `Low` |

A blank saved preset defaults to `Middle`. An unknown non-blank code causes the match to fail. No matching item yields an empty string and the player sets no data source.

### 5.2 List Selection Rules

Given the computed phase and `game_end_f`:

| Condition | Selected List |
|-----------|---------------|
| `game_end_f == "1"` AND `missed_urls` is non-empty | `missed_urls` |
| `game_end_f == "1"` AND `missed_urls` is empty | `live_urls` (no catch-up available) |
| Phase is `PLAYING` | `live_urls` |
| Phase is `FINISHED_MISSED_MATCH` AND `game_end_f != "1"` | `live_urls` |
| Phase is `IN_FUTURE`, `FINISHED`, or `CANCELLED` AND `game_end_f != "1"` | None (empty string) |

The first condition check handles VOD/catch-up: if the game has ended and there's a missed URL list, prefer that. The second fallback ensures that even finished games with no missed URLs still get a playable URL from the live list.

### 5.3 CDN Path Structure

Stream URLs follow a consistent CDN path pattern:

```
https://basketlive-live-sni.akamaized.net/live/ch{CHANNEL_ID}/{MASTER_PLAYLIST}.m3u8
```

The master playlist references a media playlist URI (relative) like:

```
{UUID_PREFIX}/cbc1/m2ts/{SUBDIR}/{MEDIA_PLAYLIST}.m3u8
```

Segments are addressed as:

```
https://basketlive-live-sni.akamaized.net/live/ch{CHANNEL_ID}/{UUID_PREFIX}/cbc1/m2ts/{SUBDIR}/{SEGMENT_NUMBER}
```

Different quality levels use different subdirectories (e.g., `m2ts/0` for Full HD, `m2ts/2` for Middle, etc.). Channel ID and UUID prefix vary per game/session.

### 5.4 Quality Variant Comparison (Empirical Data)

Observed variants per game:

| Quality Preset | File Suffix | Resolution | Bandwidth | Typical Segment Size |
|---------------|-------------|------------|-----------|---------------------|
| `8M` (Full HD) | `_MAN00.m3u8` | 1920×1080 | ~6.1 Mbps | ~4.7 MB |
| `4M` (High) | `_MAN01.m3u8` | 1280×720 | ~2.1 Mbps | ~946 KB |
| `1.5M` (Middle) | `_MAN02.m3u8` | 854×480 | ~1.1 Mbps | ~542 KB |
| `500K` (Low) | `_MAN03.m3u8` | 320×180 | ~628 Kbps | ~284 KB |

Default preset for the service: `Middle` (`854×480`, ~1 Mbps). Configurable via environment variable.

---

## 6. HLS AES-128 Encryption & License Key Exchange

Streams use **HLS AES-128 segment encryption**, not widevine or playready DRM. The encryption scheme works as follows:

### 6.1 Encryption Architecture

| Layer | Mechanism |
|-------|-----------|
| Transport | HTTP(S) to Akamai CDN (CloudFront edge) — segments delivered unmodified |
| Content protection | `#EXT-X-KEY: METHOD=AES-128` tags in media playlists |
| Key source | SoftBank endpoint `/api/v1/playkey` requiring `X-BASKETLIVE-FREEKEY` header |
| Decryption | Client-side AES-128-CBC/PKCS7Padding using key bytes + tag-specified IV |

Each segment in a media playlist carries its own `#EXT-X-KEY` tag with a unique IV, incremented by 1 for each subsequent segment. One AES key covers all segments referencing the same key URI + IV pair. Key URIs rotate as the live window moves — the same URI but new IVs appearing periodically (~200 key tags in the first 30 KB of a playlist).

The playlist parser handles `KEYFORMAT=com.widevine` on a separate branch from `METHOD=AES-128`. The service deals exclusively with the AES-128 path.

### 6.2 The Playlist URL Is Requested as Returned

The chosen stream URL from the game detail is passed to the player unchanged. `MediaPlayer.setDataSource(Context, Uri)` accepts a null header map — no custom headers are prepended to playlist or segment requests. The license credential is **player configuration**, not a suffix appended to the URL.

### 6.3 Key Request Flow

The media playlist contains `#EXT-X-KEY` tags pointing to:

```
URI="https://basketball.mb.softbank.jp/api/v1/playkey"
```

A GET request to this URI **without** the FREEKEY credential returns:

```
HTTP/400
Content-Type: application/json
Body: {"message":"forbidden"}
```

With the FREEKEY credential:

```
GET https://basketball.mb.softbank.jp/api/v1/playkey \
  -H "User-Agent: okhttp/4.9.3" \
  -H "X-BASKETLIVE-FREEKEY: Jc3_6cnTpWfZxoUPaBUgtHolu4p_xl72uyMkoRevA5Be6h_OshXi3ztp7PCOkA7Y"
```

Returns:
```
HTTP/200
Content-Type: binary/octet-stream
Body: 16 bytes raw binary
```

The response is exactly 16 bytes — the raw AES-128 decryption key. Hex dump example: `eaa206f571f2411363be37b46b55118e`.

The key is **static and identical** regardless of which game, quality, channel ID, or media playlist is queried. The license server always returns the same 16-byte key to anyone presenting this credential.

### 6.4 Where the Credential Lives

The hardcoded FREEKEY credential appears at line 559 of `decompiled/sources/jp/co/bleague/data/L0.java`, method `k()` (lines 556–561):

```java
public String k() {
    String strA = n0().a();
    if (strA == null || kotlin.text.p.s(strA)) {
        return "X-BASKETLIVE-FREEKEY: <credential-string>";
    }
    return "X-BASKETLIVE-ACCESSTOKEN: - " + strA;
}
```

When no stored SBID access token exists (method `n0().a()` returns null or empty), the method returns the literal beginning with `X-BASKETLIVE-FREEKEY:` followed by the credential string. This is the value injected in all key requests.

When a stored token exists, the return value changes to `X-BASKETLIVE-ACCESSTOKEN: - <token>`. The service operates without a stored session, so the FREEKEY path is always taken.

### 6.5 Decryption Implementation

Segment decryption follows the pattern verified in `C2242ek` from the decompiled binary:

```python
from Crypto.Cipher import AES

aes_key = bytes.fromhex("<16-byte hex key>")
iv = bytes.fromhex("<IV from #EXT-X-KEY tag, strip 0x prefix>")

cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
decrypted = cipher.decrypt(encrypted_segment_bytes)

# Strip PKCS7 padding (as C2242ek does)
pad_len = decrypted[-1]
if 1 <= pad_len <= 188:
    decrypted = decrypted[:-pad_len]

# Result starts with 0x47 (MPEG-TS sync byte)
assert decrypted[0] == 0x47
```

After decryption, ffprobe confirms valid H.264 high-profile stream at the selected resolution.

### 6.6 Why Standard Players Cannot Play Raw URLs

A standard player (VLC, mpv, etc.) that receives the raw Akamai CDN URL parses the `#EXT-X-KEY` tag and makes a plain HTTP GET to `https://basketball.mb.softbank.jp/api/v1/playkey` **without custom headers**. The API returns `400 forbidden` because no credential was sent. The player can never acquire the key and playback fails or produces garbled output.

The native app avoids this by setting `PlayerConfiguration.KEY_DRM_AES_SERVER_HEADER` which injects the FREEKEY credential into the key-request headers at the JNI level before the OS HTTP client fires. Third-party players expose no equivalent hook.

### 6.7 Service Solution: Proxy Pattern

The service runs a local proxy endpoint that rewrites `#EXT-X-KEY: URI="https://...playkey"` to `URI="http://{service-host}/playkey"` inside media playlists, then answers that endpoint by injecting the FREEKEY credential on behalf of the requesting player. The player transparently receives the plaintext AES key and continues decrypting segments locally.

---

## 7. Business Logic: Live and Upcoming Games

Called as `fetch_live_games()` in the Bleague source module. Executed per request.

### Algorithm

1. **Get current UTC time**, compute `today_start = YYYY-MM-ddT00:00:00` (UTC midnight) and `today_end = YYYY-MM-ddT23:59:59` (UTC next day).
2. **Fetch day schedule** for today: `GET /api/v5/schedules?league_id=ALL&game_date={YYYYMMDD}`. Parse response. Also attempt month schedule as fallback/breadth: `GET /api/v5/schedules/month?league_id=ALL&game_month={YYYYMM}`.
3. **Filter for B.LEAGUE only**: Include only rows where `league_name` contains `B.LEAGUE ONE` or `B.LEAGUE PREMIER`. Exclude Wリーグ, W聯賽, and any non-men's entries. Skip rows where `league_name` does not match known B.LEAGUE patterns.
4. **Compute phase** for each remaining game: Evaluate `game_end_f` against the three timestamps using the clock rules in Section 3. Tag each game with its computed phase (`PLAYING`, `FINISHED_MISSED_MATCH`, `IN_FUTURE`, `IN_FUTURE_ONE_DAY`).
5. **Select games by category**: Include games with phase `PLAYING`, `FINISHED_MISSED_MATCH`, `IN_FUTURE`, or `IN_FUTURE_ONE_DAY`. Exclude `FINISHED` and `CANCELLED`.
6. **Enrich with team data**: Look up `home_team_id` and `away_team_id` in the team catalog index (Section 4) to obtain English names. Also build Japanese names.
7. **Resolve streams**: For games with phase `PLAYING` or `FINISHED_MISSED_MATCH`:
   - Call `/api/v9/game/{game_id}` to fetch `live_urls` and `missed_urls`
   - Apply list selection rules (Section 5.2)
   - Select `Middle` quality by default
   - Store resolved CDN path in `stream_url`
   - All other phases get `stream_url = null`
8. **Build canonical game dict** (Section 20).
9. **Sort** by `start_time_utc` ascending (earliest first, converting `game_date + game_time` to ISO datetime).
10. **Return** aggregated list.

Schedule failures are non-fatal: if the day call throws, the month call result may still yield games. Per-league filtering silently drops non-B.LEAGUE entries.

### Input/Output

Input: Current UTC datetime. Auth headers (blank token).
Output: `[dict]` list of enriched game records, each including `match_id`, `game_end_f`, computed `phase`, `live_urls`, `missed_urls`, resolved `stream_url`, and team metadata.

### Caching

Schedule data is cached for **60 seconds** (`TTLCache(maxsize=256, ttl=60)`) — statuses change during live games. Stream URLs are never cached (signed tokens expire, CDN paths require fresh game-detail calls).

---

## 8. Business Logic: Completed Games

Called as `fetch_completed_games()` in the Bleague source module.

### Algorithm

1. **Get current UTC time**, compute lookback range: `from_date = now minus 3 days`, formatted as `YYYYMMDD`.
2. **Fetch month schedules** covering the lookback window. Iterate `YYYYMM` months from `from_date` to `now`, fetching `/api/v5/schedules/month?league_id=ALL&game_month={YYYYMM}` for each month. Aggregate results.
3. **Filter for B.LEAGUE only**: Same pattern as live games.
4. **Compute phase** for each remaining game using clock rules.
5. **Select games by category**: Include only phase `FINISHED`. Exclude `CANCELLED` and all active phases.
6. **Resolve streams**: For each finished game:
   - Call `/api/v9/game/{game_id}`
   - Check if `missed_urls` is non-empty
   - If yes: select from `missed_urls` (catch-up content)
   - If empty: select from `live_urls` (legacy fallback, game played without catch-up)
   - Select `Middle` quality
   - Categorize: `full_game` (primary replay), `highlights` if separate highlight URL exists
7. **Build canonical game dict** with nested `videos` dict:
   ```python
   {
       "videos": {
           "full_game": {"video_id": ..., "title": ..., "stream_url": "..."},
           "highlights": {"video_id": ..., "title": ..., "stream_url": "..."}
       }
   }
   ```
8. **Sort** by `start_time_utc` descending (most recent first).
9. **Return** aggregated list.

Games without any stream URLs (finished but with neither live nor missed URLs) have `videos = null` and are excluded from playlist output.

### Video Classification (Adapted from NBL1)

Since B.LEAGUE does not classify videos by title text parsing (unlike NBL1's sportradar endpoint), classification is driven by the presence and structure of the URL lists:

| Source List | Type Key | Meaning |
|-------------|----------|---------|
| `missed_urls` (non-empty) | `full_game` | Catch-up playback of complete game |
| `live_urls` when `missed_urls` empty | `full_game` | Legacy fallback, same content as live |
| Separate highlight URLs (rare) | `highlights` | Highlight reel (if API provides distinct entry) |

Unlike NBL1, B.LEAGUE has no multi-type categorization from a single sportradar-style endpoint. The `missed_urls` list IS the replay content. There may not be separate condensed/highlights types — the structure differs significantly from NBL1's Sportradar-based system.

---

## 9. Stream Proxy & Playkey Endpoint

The proxy endpoints bridge third-party players (VLC, mpv, TiviMate, etc.) to the encrypted B.LEAGUE streams.

### 9.1 Proxy Architecture

The service exposes three proxy endpoints, each handling a different layer of the streaming chain:

| Endpoint | Purpose | Modification |
|----------|---------|-------------|
| `/stream/{game_id}` | Resolve and redirect to HLS master | Fresh game-detail lookup, selects Middle quality URL |
| `/{cdn_path}/*.m3u8` | Forward and patch media playlists | Rewrites `#EXT-X-KEY` URI from SoftBank → `/playkey` |
| `/{cdn_path}/<segment>` | Forward segments unchanged | Binary passthrough |
| `/playkey` | Serve AES-124 decryption key | Injects FREEKEY credential, returns 16-byte binary |

### 9.2 The Playkey Endpoint

```
GET https://{service-host}/playkey
```

This endpoint serves the 16-byte AES-128 key extracted from the freekey credential exchange. The implementation:

```python
# Pseudocode
async def handle_playkey():
    resp = await httpx.get(
        "https://basketball.mb.softbank.jp/api/v1/playkey",
        headers={
            "User-Agent": "okhttp/4.9.3",
            "X-BASKETLIVE-FREEKEY": "<hardcoded-freekey>",
        }
    )
    assert len(resp.content) == 16
    return PlainTextResponse(resp.content, media_type="binary/octet-stream")
```

The key is **static** — every request returns identical bytes. However, the credential request is made on-demand (not cached at startup) because the response should reflect current server behavior and because the cost is trivial (one HTTP round-trip returning 16 bytes).

### 9.3 Playlist Rewrite Logic

Media playlists fetched through the proxy contain rewritten `#EXT-X-KEY` lines:

Original (from CDN):
```
#EXT-X-KEY:METHOD=AES-128,URI="https://basketball.mb.softbank.jp/api/v1/playkey",IV=0x...
```

Rewritten (through proxy):
```
#EXT-X-KEY:METHOD=AES-128,URI="http://{service-host}/playkey",IV=0x...
```

Only the URI portion changes. The IV remains untouched. All other playlist directives pass through unmodified. Detection: search for the literal softbank playkey URI in the playlist text and replace with the service-host version.

Path handling:
- Incoming path starting with `/ch{NNN}/` → strip leading `/`, prepend CDN base URL
- Incoming path not starting with `/ch{NNN}/` (from VLC resolving relative URIs inside m3u8) → prepend `ch{NNN}/` before constructing CDN URL

### 9.4 Stream Resolution Proxy

```
GET https://{service-host}/stream/{game_id}
```

This endpoint performs a fresh game-detail lookup, selects the Middle-quality URL from the appropriate list (live or missed), and issues a 302 RedirectResponse to the Akamai CDN URL. This mirrors NBL1's `/stream/{match_id}` pattern but adapts to B.LEAGUE's architecture where the CDN path is embedded directly in the game-detail response rather than resolved via a separate stream-ID endpoint.

Per-request resolution: no caching of resolved URLs. Each viewer gets their own redirect chain.

### 9.5 Geo-Restriction Implications

The proxy and playkey endpoints run on a Japan-region Cloud Run instance. Both the Akamai CDN forwarder and the SoftBank playkey request originate from within Japan, avoiding the `403` load-balancer block. Without this geographic positioning, neither CDN assets nor the license key can be obtained.

---

## 10. Playlist Generation (BLeague Portion)

The `/pl/` endpoint builds a complete M3U playlist. Here's the BLeague-specific rendering logic:

### BLeague Entry Structure

For each game:
```m3u8
#EXTINF:-1 group-title="{Conference} Live" tvg-id="{game_id}" tvg-logo="{logo}",{label}
http://{base}/stream/{game_id}
```

Where `{label}` is generated similarly to NBL1:

| Condition | Label Format |
|-----------|-------------|
| Score available | `{home_jp} vs {away_jp} ({score}-{score}) - DD/MM HH:MM` |
| No score | `{home_jp} vs {away_jp} - Season R{round}` |
| Has English names | Optional: add `(EN: {home_en} vs {away_en})` |

Local time displayed by converting `game_date + game_time` (already in JST/GMT+9) to readable format: `DD/MM HH:MM`.

Note: Unlike NBL1 which converts UTC→local, B.LEAGUE times are already in local time (`HH:mm:ss` represents tip time in JST), so no timezone conversion is needed.

### Grouping Rules

| Game Phase | M3U Group |
|------------|-----------|
| `PLAYING` | `{League} Live` (e.g. `B.LEAGUE ONE Live`) |
| `FINISHED_MISSED_MATCH` | `{League} Catch-Up` |
| `IN_FUTURE`, `IN_FUTURE_ONE_DAY` | `{League} Upcoming` |
| `FINISHED` | `{League} Completed` |

### Stream URL Handling

| Game Phase | URL in Playlist |
|------------|-----------------|
| Live (`PLAYING`) | Proxy redirect `/{base}/stream/{game_id}` (resolves fresh on play) |
| Catch-up (`FINISHED_MISSED_MATCH`) | Proxy redirect `/{base}/stream/{game_id}` (uses missed_urls) |
| Completed (`FINISHED`) | Actual Akamai CDN URL embedded directly (from `missed_urls` or `live_urls`) |
| Upcoming (`IN_FUTURE`) | Proxy redirect `/{base}/stream/{game_id}` (will 404 if no stream resolves yet) |

### Game Ordering in Playlist

BLeague games appear after all TV channels and are ordered:

1. Live games sorted by `game_date + game_time` ascending
2. Catch-up games sorted chronologically
3. Upcoming games sorted by `game_date + game_time` ascending
4. Completed games sorted by `game_date + game_time` descending

### Channel ID Format

```python
def _channel_id(game: dict, vtype: str | None = None) -> str:
    gid = game.get("match_id", "")
    if vtype:
        return f"{gid}.{vtype}"
    return gid
```

Used as `tvg-id` attribute linking to EPG `<programme>` elements.

### Differences from NBL1 Playlist

| Aspect | NBL1 | BLeague |
|--------|------|---------|
| Timezone | UTC internal, convert to GMT+7 for display | Already in JST, no conversion needed |
| Stream URL | Resolved via video_id → stream ID endpoint | Embedded directly in game-detail `live_urls`/`missed_urls` |
| Signed tokens | Akamai `hdnea=` (expires) | No signing on CDN paths themselves; encryption handled separately |
| Video types | Full game, condensed, highlights (from sportradar) | Single `missed_urls` (catch-up) per game |
| Labels | English team names | Japanese team names primary; English optional |

---

## 11. EPG Generation (BLeague Portion)

The `/pl/epg.xml` endpoint generates XMLTV-format program guides. BLeague games contribute both `<channel>` and `<programme>` elements.

### Programme Duration Rule

B.League programme slots use a **fixed duration of 2 hours** (`PROGRAMME_DURATION = timedelta(hours=2)`), same as NBL1. This applies uniformly regardless of actual game length.

### EPG Window

Same as NBL1: window spans from today 00:00 UTC to the day after 00:00 UTC. Two-day total coverage.

### XMLTV Structure for Live/Upcoming Games

For each qualifying game, three programme slots are created:

```xml
<!-- Channel definition -->
<channel id="{game_id}">
    <display-name>{Home} vs {Away} - HH:MM</display-name>
    <icon src="{logo_url}"/>
</channel>

<!-- Upcoming slot: from window start to game start -->
<programme channel="{game_id}" start="{window_start_utc}" stop="{game_start_utc}">
    <title lang="en">Upcoming: {Home} vs {Away}</title>
    <desc lang="en">{home_jp} take on {away_jp} at {venue}.</desc>
    <category>Basketball</category>
</programme>

<!-- Live/Current slot: game start to game end (fixed 2h) -->
<programme channel="{game_id}" start="{game_start_utc}" stop="{game_end_utc}">
    <title lang="en">{Home} vs {Away}</title>
    <desc lang="en">{home_jp} are taking on {away_jp} in this round X match.</desc>
    <category>Basketball</category>
</programme>

<!-- Completed slot: from game end to window end -->
<programme channel="{game_id}" start="{game_end_utc}" stop="{window_end_utc}">
    <title lang="en">Completed: {Home} vs {Away}</title>
    <desc lang="en">{home_jp} took on {away_jp} in this round X match.</desc>
    <category>Basketball</category>
</programme>
```

### Description Variations by Phase

The description function varies sentence construction based on whether the game is upcoming, live, or completed. Uses Japanese team names primarily, with optional English parenthetical. Includes venue, round number, and season context where available.

### Timezone Handling in EPG

Unlike the NBL1 service which stores all times internally as UTC and converts only for display, B.LEAGUE data comes pre-localised. The `game_date + game_time` values represent JST (GMT+9). The EPG generator must convert these to UTC for XMLTV compliance:

```python
def _utc_from_jst(date_str, time_str):
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    return dt.astimezone(timezone.utc)
```

The XMLTV `<programme start="...">` attributes use UTC format (`YYYYMMDDHHMMSS +0000`).

### Completed Games with Videos

Each video type generates its own channel:

```xml
<channel id="{game_id}.full_game">
    <display-name>{Home} vs {Away} [Catch-Up]</display-name>
</channel>
<programme channel="{game_id}.full_game" start="{start_utc}" stop="{end_utc}">
    <title>{label} [Catch-Up]</title>
    <desc lang="en">{home_jp} took on {away_jp}...</desc>
    <category>Basketball</category>
</programme>
```

Same for `.highlights` variant if available. Stop time = start + 2 hours.

---

## 12. Concurrency Model

### Parallelization Strategy

The BLeague module uses parallel fetching similar to NBL1:

| Parallel Scope | Mechanism | Count |
|---------------|-----------|-------|
| Month schedule fetching | `asyncio.gather(*months)` | Up to ~2–3 simultaneous (per-month-range queries) |
| Game detail fetching | Concurrent for all qualifying games | Variable, capped by semaphore |
| Team catalog fetch | Single call (cached) | 1 |

Unlike NBL1's fixed 5-conference parallelism, BLeague uses time-based parallelism (concurrent month queries for the lookback window). The number depends on how many months span the query range.

### Rate Limiting

```python
upstream_sem = asyncio.Semaphore(20)
```

Shared global semaphore throttles outbound calls. Applied to all `fetch_json()` and stream resolution calls. Prevents thundering-herd during peak load.

### Lock Usage

Double-checked locking for singleton cache writes:

```python
_lock = asyncio.Lock()
async with _lock:
    if "key" not in cache:
        cache["key"] = await expensive_fetch()
```

Prevents race conditions when multiple coroutines attempt to populate the same cache entry.

### Memory Registry

Same pattern as NBL1:

```python
match_ref_map: dict[str, str] = {}  # module-level global
```

Maps `game_id` → useful context during processing. Cleared on shutdown. Optionally persisted to Firestore.

---

## 13. Caching Strategy

All caches use `cachetools.TTLCache` — LRU-restrained, TTL-expired, in-process memory dictionaries. No external Redis/Memcached dependency.

| Data | Cache Key | TTL | Rationale |
|------|-----------|-----|-----------|
| Team catalog | Full API URL | 3600s (1h) | Teams don't change |
| Schedule data (day) | Full API URL | 60s | Game statuses change rapidly |
| Schedule data (month) | Full API URL | 60s | Same reason |
| Game detail data | `/api/v9/game/{id}` | 60s | Stream URLs change between games |
| Stream URLs | None (per-request) | — | CDN paths may vary; game state changes |
| Playkey response | Instance-level (volatile) | Process lifetime | Key is static 16 bytes |

---

## 14. Error Handling

Errors follow a tiered approach from tolerant to fatal:

| Failure | Behavior |
|---------|----------|
| Single month schedule query fails | Skip that month, include results from others |
| Game detail fetch fails for a match | Set `stream_url = null`, continue with other games |
| Stream resolution fails | Set `stream_url = null` (game shown without playable link) |
| Upstream returns non-200 | Raise `UpstreamError(status=response_code)` → caught by handler → `502 JSON` |
| Connection timeout | Caught → generic `UpstreamError()` → `502 {"error":"upstream_unavailable"}` |
| Invalid JSON response | Exception bubbles up, caught by handler → `502` |
| 403 from CDN (geo-block) | Fatal for that request; retry with Japan-originated request (automatic on Cloud Run asia-northeast1) |
| Playkey returns non-16-byte | Log error; player receives malformed key or connection error |

The 403 geo-block deserves special handling: if a request to Akamai or SoftBank returns `403` from a Cloud Run instance outside Japan, the entire streaming pipeline breaks. Deploying to `asia-northeast1` eliminates this class of error.

---

## 15. Constants & Configuration

All configurable constants live at module level in `sources/bleague/__init__.py`:

```python
CDN_BASE         = "https://basketlive-live-sni.akamaized.net/live/"
API_BASE         = "https://api.basketball.mb.softbank.jp/api"

LEAGUE_IDS       = {"ALL": "ALL", "BLEAGUE": "BLEAGUE"}
VALID_LEAGUES    = ["ALL", "BLEAGUE"]          # case-sensitive

B_LEAGUE_NAMES   = ["B.LEAGUE ONE", "B.LEAGUE PREMIER"]  # filter targets

GAME_END_FLAGS   = {"9": "before", "0": "during", "1": "over", "99": "cancel"}
LIVE_PHASES      = {"PLAYING", "FINISHED_MISSED_MATCH", "IN_FUTURE", "IN_FUTURE_ONE_DAY"}
COMPLETED_PHASES = {"FINISHED"}
EXCLUDED_PHASES  = {"CANCELLED"}

AUTH_HEADERS     = {
    "User-Agent": "okhttp/4.9.3",
    "X-OS-TYPE": "ANDROID",
    "X-APP-VERSION": "4.0.2",
    "X-BASKETLIVE-ACCESSTOKEN": "- ",
}

FREEKEY_HEADER   = "X-BASKETLIVE-FREEKEY"
FREEKEY_VALUE    = "<extracted-from-L0.java-line-559>"

QUALITY_DEFAULT  = "Middle"
QUANTITY_MAP     = {"8M": "Full HD", "4M": "High", "1.5M": "Middle", "500K": "Low"}

# Caches
season_cache     = TTLCache(maxsize=10, ttl=86400)     # 24 hours (adapted from NBL1)
data_cache       = TTLCache(maxsize=256, ttl=60)        # 1 minute
upstream_sem     = asyncio.Semaphore(20)                # max concurrent requests
match_ref_map    = dict[str, str]()                     # in-memory game registry
```

**No runtime environment variables are needed.** All credentials and API keys are hardcoded as literal strings in source, matching the `nbl1-streams` pattern (where `Authorization: Bearer xxx.app` is a compile-time constant). This means zero infrastructure configuration beyond deploying the container.
---

## 16. Known Gaps & Edge Cases

### 16.1 English Fields Rarely Populated

The `*_e` and `*_es` fields on schedule rows and game-detail responses are frequently absent on the wire. English names are reliably available only from `/api/v3/teams`. The service should not depend on English fields being present on game-detail responses.

### 16.2 Trailing Whitespace on Wire Keys

The wire key `schedule_class_nameE ` (with trailing space) appeared in the 14 October response. Python's JSON parser strips this automatically, but if the service parses raw JSON manually or processes wire responses, this whitespace must be accounted for.

### 16.3 Static License Key

The AES-128 key is identical for all games and all channel IDs. This is unusual — typically each stream would have its own key. If SoftBank rotates this credential, the hardcoded value in `L0.java` line 559 becomes stale and all streams break until updated.

### 16.4 No Retry Logic

Failed upstream requests raise `UpstreamError` immediately. There is no exponential backoff, retry, or circuit breaker. Transient network hiccups cause immediate failures to clients.

### 16.5 Limited Fixture Coverage

The month schedule query does not enforce a `limit` parameter, but the day schedule may implicitly cap results. Games occurring outside the query date range are invisible to the service.

### 16.6 Missed/Catch-Up Window Expiry

The `missed_live_end_date` defines a finite catch-up window. After this date passes, `missed_urls` may become empty even though the game was previously watchable. The service should gracefully degrade to `live_urls` fallback (which may also be empty, yielding no playback).

### 16.7 W-League Exclusion Ambiguity

The spec says "men only." Filtering by `league_name` substring match ("B.LEAGUE") excludes Wリーグ, but additional validation should ensure no mixed-content or co-ed competition entries slip through.

---

## 17. Containerization & Deployment

### 17.1 Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Optional: Chromium for browser-based resolvers (not needed for Bleague source alone)
# RUN playwright install --with-deps chromium
COPY main.py .
COPY sources/ sources/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", 
     "--proxy-headers", "--forwarded-allow-ips", "*"]
```

Playwright Chromium is not required for the BLeague source alone (no headless browser needed — the API calls work with httpx). Include only if adding other sources that require browser automation.

### 17.2 Requirements

```
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
httpx>=0.27,<1
cachetools>=5.3,<6
google-cloud-firestore>=2.16,<3
pycryptodome>=3.20,<4    # New: for AES-128 segment decryption verification
```

### 17.3 Google Cloud Run Deployment

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT="<your-project>"
REGION="asia-northeast1"
SERVICE="bleague-streams"

# No environment variables needed — all credentials are hardcoded in source.

# Deploy (simplest possible):
gcloud config set project "$PROJECT"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1 \
  --timeout 120
```

Key deployment parameters:

| Parameter | Value | Impact |
|-----------|-------|--------|
| Region | `asia-northeast1` (Tokyo/Osaka) | Mandatory — avoids 403 geo-block from CDN and API |
| Memory | 2 GB | Sufficient for async concurrency and in-memory caches |
| Min instances | 1 | Prevents cold starts for incoming requests |
| Timeout | 120s | Allows time for multi-step upstream resolution |
| Unauthenticated | Yes | Anyone can hit the endpoints without auth |

No authentication headers or secret management is needed beyond the FREEKEY value. The API is publicly accessible with the blank token — no SBID session is required for schedule or game-detail reads.

### 17.4 Network Requirements

The service requires outbound HTTPS connectivity to:
- `api.basketball.mb.softbank.jp` — schedule and game-detail APIs
- `basketlive-live-sni.akamaized.net` — Akamai CDN (HLS master, media playlists, segments)
- `basketball.mb.softbank.jp` — license key server (`/api/v1/playkey`)

All traffic originates from a Japan-region VPC, satisfying the geo-restriction on all three hosts.

---

## 18. Testing Infrastructure

Test harnesses exercise the BLeague source independently of the FastAPI app, mirroring the NBL1 pattern.

### `test_schedule.py` — Game Date Identification Trace

Prints detailed per-game filtering decisions:

```
>>> Filtering for today (2026-10-14):
✓ IWATE BIGBULLS vs EARTHFRIENDS TOKYO Z
  Start: 2026-10-14 19:05:00
  Status: during (0)
  In time range: True, Valid phase: PLAYING

✗ アシン ウィングス vs 広島イーグレッツ
  Start: 2026-10-14 17:00:00
  Status: over (1)
  Phase: FINISHED
  Included: False (completed, goes to /games/completed)
```

Shows first 20 fixtures, counts remaining, displays final qualification count.

### `test_teams.py` — Team Catalog Index Verification

Validates that the team catalog index correctly maps `team_id` → English names for known B.LEAGUE teams:

```
>>> Team catalog loaded: 55 teams across 3 league types
✓ ID=709: 岩手ビッグブルズ → IWATE BIGBULLS (IWATE)
✓ ID=715: アースフレンズ東京Z → EARTHFRIENDS TOKYO Z (TOKYO-Z)
✓ ID=702: レバンガ北海道 → LEVANGA HOKKAIDO (HOKKAIDO)
```

### `test_playkey.py` — License Key Verification

Confirms the hardcoded credential produces a valid 16-byte key:

```
>>> Sending FREEKEY credential...
>>> Got AES-128 key: eaa206f571f2411363be37b46b55118e (16 bytes)
>>> Decrypting sample segment...
>>> First byte: 0x47 ✓ Valid MPEG-TS sync byte
```

---

## 19. Endpoint Summary

| Endpoint | Method | Returns | BLeague Data |
|----------|--------|---------|-------------|
| `/games/live` | GET | JSON | Today's live + upcoming B.LEAGUE men's games with computed phases and stream URLs |
| `/games/completed` | GET | JSON | Last 3 days' finished B.LEAGUE games with catch-up URLs |
| `/stream/{game_id}` | GET | 302 redirect | Resolves to Akamai HLS master URL on demand |
| `/pl/` | GET | M3U playlist | BLeague entries alongside all other sources |
| `/pl/epg.xml` | GET | XMLTV guide | BLeague channels and programmes alongside all other sources |
| `/health` | GET | `{"status":"ok"}` | Healthcheck (source-agnostic) |
| `/playkey` | GET | 16-byte binary | AES-128 decryption key (injects FREEKEY on upstream request) |

Notes:
- Unlike NBL1, BLeague games use integer `game_id` (as string) rather than UUID `match_id` for identifiers
- Stream resolution returns Akamai CDN paths directly (not a separate stream-ID lookup)
- The `/playkey` endpoint is unique to BLeague — NBL1 has no equivalent since its streams are clear HLS with signed URLs
- BLeague does not expose separate VOD endpoints like `/sbs/{slug}` or `/plex/{slug}` — those belong to other source modules

---

## 20. Canonical Game Dictionary Schema

Every game record flows through a unified schema consumed by all downstream components:

```python
{
    "match_id":               str,       # game_id from schedule row
    "international_ref":      str,       # alias for game_id (kept for compat with NBL1 patterns)
    "game_end_f":             str,       # raw server flag: "9", "0", "1", "99"
    "computed_phase":         str,       # PLAYING, IN_FUTURE, IN_FUTURE_ONE_DAY, 
                                         # FINISHED_MISSED_MATCH, FINISHED, CANCELLED
    "start_time_utc":         str,       # ISO datetime UTC
    "start_time_local":       str,       # JST time string (HH:MM)
    "round":                  str | None,# Round number
    "fixture_type":           str,       # Game classification
    "season_name":            str,       # Season label
    "venue":                  str | None,# Venue name (Japanese)
    "conference":             str,       # League label: "B.LEAGUE ONE" etc.
    "home_team": {
        "id":                 str,       # home_team_id
        "name_jp":            str,       # Japanese name
        "name_en":            str | None,# Full English (from catalog lookup)
        "name_es":            str | None,# Short English (from catalog lookup)
        "score":              str | None,# Score (null if not started)
        "logo_url":           str | None,# Team logo
    },
    "away_team": {             # Same structure as home_team
        ...
    },
    "live_urls":              list[dict] | None,  # From game detail
    "missed_urls":            list[dict] | None,  # From game detail
    "stream_url":             str | None,         # Resolved CDN path (Middle quality)
}
```

Fields annotated as `None` when unavailable (e.g., English names absent from catalog, scores null for scheduled games, stream URLs for upcoming games with no detail lookup yet).
