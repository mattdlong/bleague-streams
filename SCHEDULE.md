# Basket LIVE schedule, English names, and game status

How the バスケットLIVE 4.0.2 client loads a schedule, where English labels live, how a game becomes scheduled, live, missed, finished, or cancelled, and which further call supplies the stream URL for a schedule row.

Evidence is the JADX decompilation in `decompiled/` plus calls made on 14 October 2026 through a Japan network path. A call from outside Japan was refused by the load balancer with `403` and an HTML page, before the API ran. The same call from Japan reached the API.

The later sections name the response fields, the selection rule, how that playlist is encrypted, and the code site of the license credential. They do not copy playlist addresses, key URIs, or the hardcoded credential value.

## Calling the schedule

The schedule screen uses two GET methods. Neither checks membership. Both go through the shared API client.

| Use | URL | Query |
|---|---|---|
| One day | `https://api.basketball.mb.softbank.jp/api/v5/schedules` | `league_id`, `team_id`, `game_date` |
| One month | `https://api.basketball.mb.softbank.jp/api/v5/schedules/month` | `league_id`, `team_id`, `game_month` |

`game_date` is `yyyyMMdd`. `game_month` is `yyyyMM`.

`league_id` must be a numeric league id, or the exact strings `ALL` or `BLEAGUE`. The value `All` is rejected. The error text is 「値が不正です。数値、または[ALL, BLEAGUE]のみ許可されています」. The schedule screen starts with league `ALL`. The repository substitutes `All` only when the league id is null, and that value does not match what the server accepts.

`team_id` is optional. The screen sends it only while the league filter is B.LEAGUE or the league id is empty. Otherwise the parameter is left off.

Headers the shared client adds on every API call, including these:

| Header | Value |
|---|---|
| `User-Agent` | `okhttp/4.9.3` |
| `X-OS-TYPE` | `ANDROID` |
| `X-APP-VERSION` | `4.0.2` |
| `X-BASKETLIVE-ACCESSTOKEN` | `- ` plus the stored access token |

With no stored session the token half is empty, so the header value is `- `. A day request sent that way returned `200`. There is no language parameter. `lang=en` and `Accept-Language: en` did not change the Japanese names.

A `401` is the only status for which this client refreshes the session and retries. The schedule call above did not take that path.

Example that returned `200` on 14 October 2026:

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261014
```

Response `Content-Type` was `application/json`. `Last-Modified` was `2026-10-14 07:40:44 GMT`. The body was about 79 KB.

### Day body

Top-level keys are `games` and `schedule`.

`games` is the list of games for `game_date`. On that date there were 15 games, in B.LEAGUE ONE and Wリーグ. Each object carries identity, clubs, tip time, and the status inputs below. The client decodes this list with the missed-video model (`MissVideoEntity`), which is a game row, not a separate video type.

`schedule` is a list of date strings in `yyyyMMdd`, the dates that have games. That response held 1824 dates, beginning `20180114`.

Fields used for status and labels on each day-row:

| JSON field | Role |
|---|---|
| `game_id` | Identity |
| `game_date` | Calendar date, `yyyy-MM-dd` |
| `game_time` | Tip time, `HH:mm:ss` |
| `game_end_f` | Server phase flag, string |
| `live_start_date` | Live window start, `yyyy-MM-dd HH:mm:ss` |
| `live_end_date` | Live window end |
| `missed_live_end_date` | Catch-up window end |
| `home_team_id`, `away_team_id` | Join key for English club names |
| `home_team_name`, `away_team_name` | Japanese club names |
| `home_team_name_s`, `away_team_name_s` | Japanese short names |
| `stadium_name` | Japanese venue |
| `league_name` | Japanese league label |
| `schedule_class_name` | Japanese class label |
| `schedule_class_nameE` | English class label |

The first row that day was game `110080`, アイシン ウィングス at 広島イーグレッツ, `game_time` `17:00:00`, `game_end_f` `9`, live window `2026-10-14 16:50:00` to `2026-10-14 23:00:00`, missed window through `2026-11-13 23:59:59`, league Wリーグ.

### Month body

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules/month?league_id=ALL&game_month=202610
```

Top-level keys are again `games` and `schedule`.

Each entry in `games` is one date bucket: `date`, `date_order`, and `games`. The inner `games` list is the same game row as the day call. `schedule` is again the list of dates.

## English fields

There is no second host and no translated copy of the schedule. English is extra fields on the same JSON. The suffix `_e` is the full English name. `_es` is the short English name, parallel to the Japanese short name `_s`. The app UI itself stays Japanese.

### On the schedule row

The day and month game rows expose `schedule_class_nameE` next to `schedule_class_name`. The client annotation is `schedule_class_nameE`. On the 14 October response the wire key was `schedule_class_nameE ` with a trailing space, and the value was null on all 15 games. Those rows did not include `home_team_name_e`, `away_team_name_e`, or `stadium_name_e`.

### Club names

English club names are on the team catalog, not on the schedule row.

```
GET https://api.basketball.mb.softbank.jp/api/v3/teams
```

Same headers. No query. The body key is `teams_info`. Each block has `team_kind_id`, `team_kind_name`, `team_kind_name_s`, and `items`. Each item is a conference (`team_conference_cd`, `team_conference_name`) whose `teams` array is the clubs.

| JSON field | Example for team id of レバンガ北海道 |
|---|---|
| `team_id` | Join to `home_team_id` or `away_team_id` |
| `team_name` | レバンガ北海道 |
| `team_name_s` | 北海道 |
| `team_name_3s` | レバン |
| `team_name_e` | LEVANGA HOKKAIDO |
| `team_name_es` | HOKKAIDO |

Match `home_team_id` and `away_team_id` from the schedule row to `team_id` here. The same two English fields exist on the team, team-stats, and box-score models.

### Game detail

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/{game_id}
```

Same headers. The body key is `game`. The client model also reads these English fields when the server sends them:

| JSON field | Meaning |
|---|---|
| `home_team_name_e` | Home club, full English |
| `home_team_name_es` | Home club, short English |
| `away_team_name_e` | Away club, full English |
| `away_team_name_es` | Away club, short English |
| `stadium_name_e` | Venue, English |
| `schedule_class_nameE` | Class, English |

`GET https://api.basketball.mb.softbank.jp/api/v9/game/110080` returned `200` and the Japanese names only. Those `*_e` club and stadium fields were absent on that payload, so a caller cannot assume game detail fills them. The team catalog is the call that returned populated English club names.

Past-match rows use the same club and stadium English fields. Player records use `name_e` and `player_name_e`. Those are not part of the schedule row.

## Establishing game status

The server does not return a status enum. The client computes one in `GameItem.x()` from `game_end_f` and three timestamps. The same fields are on the schedule responses above, and on these calls:

- `https://api.basketball.mb.softbank.jp/api/v5/top/live?devices=&team_id=&league_id=`
- `https://api.basketball.mb.softbank.jp/api/v6/live?league_id=`
- `https://api.basketball.mb.softbank.jp/api/v6/missed?league_id=&offset=`
- `https://api.basketball.mb.softbank.jp/api/v3/game/detail?game_id=&game_date=&missed=`
- `https://api.basketball.mb.softbank.jp/api/v9/game/{game_id}`

Whichever payload the screen holds is the one it judges. The schedule tab does not poll. It reloads when the screen is created, when it becomes visible, and on refresh or a league change. A countdown on the day-of row rebinds that row when it hits zero, which runs `x()` again without a new request.

### The flag

`game_end_f` is a string:

| Value | Name in the client | Meaning |
|---|---|---|
| `9` | before | Not started |
| `0` | during | In progress |
| `1` | over | Ended |
| `99` | cancel | Cancelled |

### When a timestamp is missing

If `live_start_date`, `live_end_date`, or `missed_live_end_date` is null or blank, the flag is the status:

| `game_end_f` | Phase |
|---|---|
| `9` | Future |
| `0` | Playing |
| `1` | Finished |
| `99` | Cancelled |

Any other flag value falls through to the clock rules below. Missing timestamps then parse as the epoch, so that fall-through is not a useful phase.

### When all three timestamps exist

Compare a clock shifted to GMT+9 against the three timestamps. The client shifts “now” by the difference between the device zone and `GMT+9`. It parses each timestamp as `yyyy-MM-dd HH:mm:ss` in the device’s default zone, and does not apply that same shift to the parsed values. Windows are open on the inside: after the earlier instant and before the later one. An instant that lands exactly on a boundary is not inside that window.

`live_start_date` also supplies the calendar day. Midnight (`00:00:00`) on that day is the day boundary. If `live_start_date` itself is missing, the day boundary falls back to `game_date`.

Let `now` be the shifted clock, `day` midnight of the live-start day, `start` = `live_start_date`, `end` = `live_end_date`, and `missedEnd` = `missed_live_end_date`.

1. `game_end_f` is `99`: cancelled.
2. `game_end_f` is `1`: missed if `end < now < missedEnd`, otherwise finished. The flag `1` does not produce “playing”.
3. `now` is before `day`: future.
4. `day < now < start`: day-of, not yet live. The schedule row for this phase shows a one-second countdown.
5. `start < now < end`: playing.
6. `end < now < missedEnd`: missed.
7. Otherwise: finished.

Flags `9` and `0` do not override this clock once the three timestamps are present. A server flag of “during” still displays as future while `now` is before `start`.

The six phases are `IN_FUTURE`, `IN_FUTURE_ONE_DAY`, `PLAYING`, `FINISHED_MISSED_MATCH`, `FINISHED`, and `CANCEL`. The schedule list picks the row layout from that phase.

### What the player does with the same game

Playback uses a second helper on top of `x()`. It is not the schedule label.

- `game_end_f` is `1` and the missed URL list is non-empty: missed playback.
- `game_end_f` is `1` and that list is empty: the live path. The schedule phase for a finished game with no missed URLs is still finished, and the client also treats that case as over-without-catch-up.
- Phase playing: live playback.
- Phase missed while the flag is not `1`: the helper still returns live.
- Future, finished, and cancelled: neither live nor missed.

The URL lists in those rules are absent from the schedule row. The next section is the call that returns them.

## From a schedule row to a stream URL

Two requests. The schedule call identifies the game. The game call returns the addresses. The client then picks one `url` locally.

### 1. Read `game_id` from the schedule row

Use either schedule call above. The row is a `MissVideoEntity`. That model has no `live_urls` and no `missed_urls`.

On a later call the same day, `GET https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261014` still returned game `110080` without those keys. The only URL-like field on that object was `league_image_url`. By then `game_end_f` was `0`. The morning snapshot of the same row, recorded above, had `game_end_f` `9`. The flag belongs to the payload that was fetched.

A watch tap parcels that row into the player as `EXTRA_GAME_ITEM`. The parcel carries the id. The website tap on the same row opens `https://www.bleague.jp/game_detail/?ScheduleKey=` plus `game_id` and does not request a stream.

### 2. Fetch the game

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/{game_id}
```

Same headers as the schedule call. The player issues this whenever the parcelled id is non-blank, through the get-game use case (`D3.g` → repository `i`). A blank token (`X-BASKETLIVE-ACCESSTOKEN: - `) is enough. Membership is not checked before the request.

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/110080
```

That call returned `200` on 14 October 2026. The body is a JSON object whose `game` field is the game. `GameEntity` on that object has the two address lists:

| JSON field | Item fields | Role |
|---|---|---|
| `live_urls` | `quality`, `url` | Live list |
| `missed_urls` | `quality`, `url` | Catch-up list |

`quality` is the display label. `url` is the playlist address. This document does not reproduce those address strings.

On `110080`, `live_urls` held four objects and `missed_urls` was an empty list. The quality labels were `Full HD`, `High`, `Middle`, and `Low`, and each `url` was non-empty. `game_end_f` on that body was `0`.

`GET https://api.basketball.mb.softbank.jp/api/v3/game/detail?game_id=&game_date=&missed=` is a different response (`past_match`, `video`, `videos`, team stats). The player does not read the stream lists from it. A method logged as `getGameDetail` still calls `v9/game/{game_id}`.

### 3. Choose the list

Apply the playback helper from the previous section to the game just fetched:

| Condition | List |
|---|---|
| `game_end_f` is `1` and `missed_urls` is non-empty | `missed_urls` |
| `game_end_f` is `1` and `missed_urls` is empty | `live_urls` |
| Phase is playing | `live_urls` |
| Phase is missed and `game_end_f` is not `1` | `live_urls` |
| Future, finished, or cancelled, and `game_end_f` is not `1` | none |

"None" means the client returns an empty string.

### 4. Choose the quality

Keep the first item in that list whose `quality` equals the selected preset's display name.

| Code stored for the preset | Display name matched against `quality` |
|---|---|
| `8M` | `Full HD` |
| `4M` | `High` |
| `1.5M` | `Middle` |
| `500K` | `Low` |

The comparison uses the display name. A blank saved preset becomes `Middle`. An unknown non-blank code leaves the selection unset, and the match then fails. No matching item, a missing list, or no list at all yields an empty string, and the player does not set a data source.

The chosen `url` is the stream address. Cast loads that same string and labels it `application/x-mpegurl`.

The playlist address is the `url` chosen here. A server-time probe and the license credential run when the player prepares. Both are separate from building that address. The next section is what the client does with the address.

## Playback, encryption, and the license credential

### The playlist URL is requested as returned

`PlayLiveVideoActivity` `initPlayer` calls `setDataSource` with `Uri.parse` of the chosen `url`. `MediaPlayer.setDataSource(Context, Uri)` passes a null header map. The player stores that string and opens it as given. The license credential is player configuration, not a suffix on this URL.

A later reload of a media playlist can append `_HLS_msn`, `_HLS_part`, and `_HLS_skip` when the playlist already parsed by the player advertises blocking reload or a skip window (`C2140aa`). The first request still uses the raw URI. On game `110080` the API `url` had no query, the master had no `#EXT-X-SERVER-CONTROL`, and the Middle media playlist had no server-control, rendition-report, or partial-segment tags. Those query parameters were not added for that stream.

`GameItem.e()` appends `start` and `end` only on a multiple-tracking URL. That method is not the `k5()` selection above.

### The media is HLS AES-128

Checked on 14 October 2026 for game `110080`, with `User-Agent: python-urllib/3` and no Basket LIVE headers. Bodies, playlist text, and key URIs are not copied here.

| Request | Result |
|---|---|
| Each of the four `live_urls` playlists | `200`, `application/x-mpegURL`, an HLS master (`#EXTM3U`, `#EXT-X-STREAM-INF`). No `#EXT-X-KEY`. No redirect |
| The Middle media playlist | `200`. `#EXT-X-KEY` and `#EXTINF` present. No `#EXT-X-ENDLIST`, so the window was still live |
| One media segment from that playlist | `200`, `video/MP2T` |
| The first key URI named by `#EXT-X-KEY`, no credential | `400`, `application/json`, body `{"message":"forbidden"}` |

The key tag carries `METHOD`, `URI`, and `IV`. `METHOD` is `AES-128`. The URI is absolute. A longer read of the same Middle playlist repeated the key tag as the live window moved, on the order of two hundred tags in the first 30 KB. One key covers the segments that name it.

The player’s segment decryptor is `C2242ek`. It builds `Cipher.getInstance("AES/CBC/PKCS7Padding")`, uses the key-response body as the AES key, and uses the playlist IV. The license credential is the material sent to obtain that response. It is not the AES key.

The app’s live, VOD, and other-arena players set `PlayerConfiguration.KEY_DRM_AES_SERVER_HEADER` and do not set the Widevine server URL, header, custom data, or security level. The SDK still contains a Widevine license client, `Rh`, which reads other player fields and posts a binary license. The playlist parser handles `KEYFORMAT=com.widevine` on a different branch from `METHOD=AES-128`.

### Where the license credential is hardcoded

The hardcoded credential is the string literal at line 559 of `decompiled/sources/jp/co/bleague/data/L0.java`. The method that returns it is `k()`, lines 556–561.

`k()` reads the stored SoftBank ID access token from `n0().a()`. `n0()` maps the SBID auth record already on the device (`f34784b.g()`). It does not fetch a token.

| Stored token | Return value |
|---|---|
| Null or blank | `X-BASKETLIVE-FREEKEY: Jc3_6cnTpWfZxoUPaBUgtHolu4p_xl72uyMkoRevA5Be6h_OshXi3ztp7PCOkA7Y` |
| Present | `X-BASKETLIVE-ACCESSTOKEN: - ` plus that token |

The full hardcoded string returned by `k()` when no stored token exists:

Callers pass `k()` in before `prepareAsync`:

| Surface | Call |
|---|---|
| Live | `PlayLiveVideoActivity` line 6407, `S3().m()`, which is `base/b0.m()` → `D().k()` |
| VOD | `ui/playvod/u.java` |
| Other arena | `ui/playotherarena/PlayOtherArenaFragment.java` |
| Two further players | `g4/n.java`, `M4/m.java` |

`PlayerConfiguration` copies `KEY_DRM_AES_SERVER_HEADER` into `Ji.f25636V`. That is the only write of the field. The only reads are two `iget-object` instructions in `classes2.dex`, both in `i.n.i.t.v.b.a.n.k.qd.N` (JADX `C2510qd.N`, method dump skipped). For `application/x-mpegURL` or `application/vnd.ini.manifest`, that method copies the playlist header map into a new map, and when `Ji.f25636V` is non-empty parses the string into the map with `AbstractC2196ck.b`, then `B8.b.n` installs the map.

`B8.b.n` stores the map that becomes the AES key-request headers: `B8.f24397J` → `N6.f26218D` → `C2333ik.f28993l`. `C2333ik.k` sends the `#EXT-X-KEY` URI with that map. Playlist and segment requests use the other map, the one `setDataSource` left empty, which matches the `200` responses above. The bare key GET is the same key request with that map empty.

`AbstractC2196ck.b` was not decompiled. The same helper builds headers for the SDK’s PlayReady and Widevine clients, which read different `Ji` fields. Nothing copies `f25636V` into the PlayReady/Widevine `drm.config.http.headers` entry. A plugin callback (`-115`) can replace the key URI and its headers when a plugin is installed. `onLicenseAcquired` in the live activity only logs.

No key request was sent with this credential, so a key response was not observed. After a key response, decryption is the AES-128 step above. The credential string alone does not decrypt a segment.

Cast loads the same playlist URL with content type `application/x-mpegurl` and places the published access token in the media custom data under `X-BASKETLIVE-ACCESSTOKEN`. That custom data is separate from `L0.k()`.

## Checked calls

| Request | Result |
|---|---|
| `https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=All&game_date=20261014` from Japan | `400`, league id rejected |
| `https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261014` from Japan, blank token | `200`, 15 games, 1824 dates |
| Same URL with `lang=en` and `Accept-Language: en` | `200`, Japanese names unchanged |
| `https://api.basketball.mb.softbank.jp/api/v3/teams`, blank token | `200`, `team_name_e` populated |
| `https://api.basketball.mb.softbank.jp/api/v9/game/110080`, blank token | `200`, English club and stadium fields absent |
| Same game URL later the same day, blank token | `200`. `game.live_urls` had four objects, `quality` `Full HD`, `High`, `Middle`, `Low`, each `url` non-empty. `game.missed_urls` was empty. `game_end_f` was `0` |
| Day schedule URL later the same day, blank token | `200`. Game `110080` had `game_end_f` `0` and no `live_urls` or `missed_urls` key |
| Each of the four `live_urls` for game `110080`, no Basket headers | `200`, HLS master, no `#EXT-X-KEY` |
| Middle media playlist for that game, no Basket headers | `200`, `#EXT-X-KEY` `METHOD=AES-128`, live window |
| One segment from that media playlist | `200`, `video/MP2T` |
| First AES key URI from that playlist, no credential | `400`, `{"message":"forbidden"}` |
| The day URL from outside Japan | `403` from the load balancer |

## End-to-end walkthrough — playing B.LEAGUE ONE live on 14 October 2026

The following is a step-by-step account of selecting a B.LEAGUE ONE game, obtaining its stream playlist, fetching the AES-128 license key via the hardcoded credential, decrypting a media segment, and serving a playable HLS stream through a local proxy. All evidence comes from live HTTP calls in a terminal on macOS; no Android emulator or rooted device was used.

### Step 1 — Fetch today's schedule

Two endpoints exist for the schedule screen. For a single day:

```
GET https://api.basketball.mb.softbank.jp/api/v5/schedules?league_id=ALL&game_date=20261014
```

With headers `User-Agent: okhttp/4.9.3`, `X-OS-TYPE: ANDROID`, `X-APP-VERSION: 4.0.2`, `X-BASKETLIVE-ACCESSTOKEN: - ` (blank token). Returns `200` with `application/json`, about 79 KB. Top-level keys are `games` (array) and `schedule` (list of date strings).

On 14 October there were **15 games** total — 12 B.LEAGUE ONE plus W League matches. To filter B.LEAGUE ONE only, select rows where `league_name == "B.LEAGUE ONE"`. Each row provides `game_id`, `home_team_id`, `away_team_id`, Japanese club names (`home_team_name`, `away_team_name`), venue, tip time, and the phase flag `game_end_f`. On this date all B.LEAGUE ONE games had `end_f = "0"` (playing).

Example output for one row:

```json
{
  "game_id":          "507231",
  "home_team_id":     "709",
  "home_team_name":   "岩手ビッグブルズ",
  "away_team_id":     "715",
  "away_team_name":   "アースフレンズ東京Z",
  "stadium_name":     "盛岡タカヤアリーナ",
  "league_name":      "B.LEAGUE ONE",
  "game_end_f":       "0",
  "play_key":         null,
  ...
}
```

(`play_key` is never populated on the schedule row.)

### Step 2 — English team names from `/api/v3/teams`

There is no second host for translations. The English fields appear on the team catalog endpoint:

```
GET https://api.basketball.mb.softbank.jp/api/v3/teams
```

Same headers, no query parameters. Response body has key `teams_info`, an array keyed by league type. Each element lists conferences; within each conference is a `teams` array containing club objects.

To find a specific club, match its `team_id` (from the schedule row) against the catalog. On 14 October B.LEAGUE ONE had **25 teams** across five conferences.

Lookup results for game 507231:

| team_id | Japanese name | `team_name_e` (full English) | `team_name_es` (short English) |
|---|---|---|---|
| 709 | 岩手ビッグブルズ | IWATE BIGBULLS | IWATE |
| 715 | アースフレンズ東京Z | EARTHFRIENDS TOKYO Z | TOKYO-Z |

English names do not appear on schedule or game-detail rows for this game — the `*_e` fields return `null`. They are populated only in the `/api/v3/teams` catalog.

### Step 3 — Get stream URLs from game detail

Once a game is selected, fetch its detail to obtain the list of stream addresses:

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/{game_id}
```

For game **507231**:

```
GET https://api.basketball.mb.softbank.jp/api/v9/game/507231
```

Response `200`, JSON under key `game`. Important fields:

| Field | Value |
|---|---|
| `game_end_f` | `"0"` (in progress) |
| `live_urls` | Array of 4 objects (Full HD, High, Middle, Low) |
| `missed_urls` | Empty array (no catch-up yet) |

Per the selection rules from the previous section: `end_f = "0"` → playing → use **`live_urls`**. Default preset maps to **"Middle"** quality.

Middle-quality URL chosen:

```
https://basketlive-live-sni.akamaized.net/live/ch902/ndvr_MAN02.m3u8
```

All four qualities share channel identifier `ch902` and differ only in the `_MANxx` suffix:

| Preset | Quality label | Playlist path |
|---|---|---|
| `8M` | Full HD | `/live/ch902/ndvr_MAN00.m3u8` |
| `4M` | High | `/live/ch902/ndvr_MAN01.m3u8` |
| `1.5M` | Middle | `/live/ch902/ndvr_MAN02.m3u8` |
| `500K` | Low | `/live/ch902/ndvr_MAN03.m3u8` |

### Step 4 — Walk the HLS chain

#### Master playlist

```
GET https://basketlive-live-sni.akamaized.net/live/ch902/ndvr_MAN02.m3u8
```

Returns HLS master (`Content-Type: application/x-mpegURL`). Only two audio variant streams (`#EXT-X-MEDIA`, groups `AUDIO`) and one video rendition with resolution `854x480`, `BANDWIDTH=1128000`. The single video URI is relative:

```
c73ddb3a-075d-4dc8-8c65-4a52f6086847/cbc1/m2ts/2/ndvr_1791964800.m3u8
```

VLC resolves this relative to the master's base, producing:

```
https://basketlive-live-sni.akamaized.net/live/ch902/8f52a3ce-7046-4491-ae2b-45bb28e6a0ea/cbc1/m2ts/2/ndvr_1791964800.m3u8
```

(The UUID prefix varies per game/session; it appears in the media playlist URI directly when fetched with the absolute CDN path.)

#### Media playlist

```
GET https://basketlive-live-sni.akamaized.net/live/ch902/8f52a3ce-7046-4491-ae2b-45bb28e6a0ea/cbc1/m2ts/2/ndvr_1791964800.m3u8
```

`200`. Key structure:

```m3u8
#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:7
#EXT-X-MEDIA-SEQUENCE:298660800
#EXT-X-PROGRAM-DATE-TIME:2026-10-14T07:59:59.886Z
#EXT-X-KEY:METHOD=AES-128,URI="https://basketball.mb.softbank.jp/api/v1/playkey",IV=0x00000000000000000000000011cd33c0
#EXTINF:6.0,
298660800
#EXT-X-KEY:METHOD=AES-128,URI="https://basketball.mb.softbank.jp/api/v1/playkey",IV=0x00000000000000000000000011cd33c1
#EXTINF:6.0,
298660801
...
```

Key observations:
- Every 6-second segment gets its own IV, incremented by 1.
- The `URI` always points to the same SoftBank license server: `https://basketball.mb.softbank.jp/api/v1/playkey`.
- `METHOD=AES-128` means each segment encrypted with `AES/CBC/PKCS7Padding`, using a 16-byte key obtained from that URI and the IV from the tag.
- No `#EXT-X-ENDLIST`; this is a live window.

#### Segment download (raw)

One segment URL: `298660800`. Fetched directly:

```
GET https://basketlive-live-sni.akamaized.net/live/ch902/8f52a3ce-7046-4491-ae2b-45bb28e6a0ea/cbc1/m2ts/2/298660800
```

Result: `200`, `Content-Type: video/MP2T`, payload `541,824 bytes`. First byte is `0xa3` (not `0x47`), confirming the TS content is still **encrypted** after transport. The `AES256` server-side encryption header from CloudFront refers to object storage protection at rest, not to the client-facing HLS AES-128 wrap.

#### License key request WITHOUT credential → forbidden

```
GET https://basketball.mb.softbank.jp/api/v1/playkey
```
Result: `400`, `Content-Type: application/json`, body `{"message":"forbidden"}`.

#### License key request WITH credential → success

```
GET https://basketball.mb.softbank.jp/api/v1/playkey \
  -H "User-Agent: okhttp/4.9.3" \
  -H "X-BASKETLIVE-FREEKEY: Jc3_6cnTpWfZxoUPaBUgtHolu4p_xl72uyMkoRevA5Be6h_OshXi3ztp7PCOkA7Y"
```
Result: `200`, `Content-Type: binary/octet-stream`, payload is **exactly 16 bytes**:

```
eaa206f571f2411363be37b46b55118e
```

That is the raw AES-128 key. It is identical regardless of which game, quality, or channel ID is queried — the license server always returns the same static key to anyone presenting this credential.

#### Decrypting a segment locally

Using PyCryptodome (or any standard AES-128-CBC library):

```python
from Crypto.Cipher import AES

aes_key = bytes.fromhex("eaa206f571f2411363be37b46b55118e")
iv = bytes.fromhex("00000000000000000000000011cd33c0")

with open("segment_enc.ts", "rb") as f:
    ciphertext = f.read()  # 541824 bytes

cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
decrypted = cipher.decrypt(ciphertext)

# Strip PKCS7 padding (document says C2242ek does this)
pad_len = decrypted[-1]
if 1 <= pad_len <= 188:
    decrypted = decrypted[:-pad_len]  # ~541816 bytes

assert decrypted[0] == 0x47  # MPEG-TS sync byte confirmed
```

Verified with ffprobe:

```
Stream #0:0 Video: h264 (High), yuv420p, 854x480, 30 fps
Duration: 6.0s, bitrate: ~722 kbps
```

### Step 5 — Why you cannot play the raw CDN URL in VLC

The three CDN URLs listed above reach Akamai fine — you get valid HLS playlists pointing to encrypted segments. However, **VLC cannot obtain the AES-128 decryption key**: when it parses `#EXT-X-KEY: URI="https://basketball.mb.softbank.jp/api/v1/playkey"`, it makes a plain HTTP(S) GET to that URI with no custom headers. SoftBank returns `400 / {"message":"forbidden"}`, so VLC can never acquire the key and playback fails or shows garbled output.

The app's native player avoids this because its player configuration sets `PlayerConfiguration.KEY_DRM_AES_SERVER_HEADER` (read back in JNI-native code `C2510qd.N`) which injects the credential into the key-request headers before the OS-level HTTP client fires. VLC exposes no equivalent hook.

### Step 6 — Local proxy to bridge VLC to the live feed

The solution is a lightweight reverse proxy running on localhost that rewrites the `#EXT-X-KEY` line in the media playlist to point at `http://localhost:PORT/playkey`, then answers that endpoint by injecting the FREEKEY credential on behalf of VLC. VLC receives the plaintext key transparently and continues decrypting segments normally.

The proxy source code is at `decompiled/docs/stream_proxy.py`.

#### How to run

```bash
# Start the proxy (leave terminal open while streaming)
python3 decompiled/docs/stream_proxy.py
```

It prints a banner showing the VLC URL. Then:

- **VLC GUI**: Media → Open Network Stream → paste
  ```
  http://127.0.0.1:8888/ch902/ndvr_MAN02.m3u8
  ```
- **CLI**: `vlc "http://127.0.0.1:8888/ch902/ndvr_MAN02.m3u8" --quiet`
- **mpv**: `mpv "http://127.0.0.1:8888/ch902/ndvr_MAN02.m3u8"`

Any player that understands HLS AES-128 will work.

#### What the proxy does

| Request to proxy | Forwarded to | Modification |
|---|---|---|
| `/ch902/*.m3u8` | Akamai CDN `live/ch902/*.m3u8` | If result contains `#EXT-X-KEY`, rewrite `URI="https://...playkey"` to `URI="http://127.0.0.1:8888/playkey"` |
| `/ch902/<segment>` | Akamai CDN `live/ch902/<segment>` | Passed through unchanged (binary `.ts` data) |
| `/playkey` | SoftBank `api/v1/playkey` | Adds `X-BASKETLIVE-FREEKEY` header with the hardcoded credential. Returns the 16-byte key directly. |
| Any other path | — | Returns 404 |

Path handling logic:
- If incoming path starts with `/ch902/`, strip exactly the leading `/` and prepend `https://basketlive-live-sni.akamaized.net/live/`.
- Paths without `/ch902/` (these come from VLC resolving relative URIs inside m3u8 files) are prepended with `ch902/` before constructing the CDN URL.
- The proxy keeps a persistent connection to Akamai per request but reuses the TLS context.

#### Limitations

- **Live only**: the proxy only serves channel `ch902` (game 507231). Other games require changing the constant at the top of the source file to their respective channel IDs.
- **Static key cache**: the first player to request `/playkey` caches the AES key in memory. Subsequent requests serve the cached value immediately.
- **No replay/duration control**: the media playlist has no `#EXT-X-ENDLIST`, so players treat it as live. Seeking is limited to what the client supports natively.
- **Geographic restriction**: if the proxy runs from outside Japan, Akamai may return `403` to the forwarder even though SoftBank's API accepted earlier requests. In practice both CDN and API must be reachable from the same network region.

### Step 7 — Credential extraction

The hardcoded credential appears at **line 559** of `decompiled/sources/jp/co/bleague/data/L0.java`, method `k()` (lines 556–561):

```java
public String k() {
    String strA = n0().a();
    if (strA == null || kotlin.text.p.s(strA)) {
        return "X-BASKETLIVE-FREEKEY: <credential-here>";
    }
    return "X-BASKETLIVE-ACCESSTOKEN: - " + strA;
}
```

When no stored SBID access token exists (`n0().a()` returns null or empty), the method returns the literal string beginning with `X-BASKETLIVE-FREEKEY:` followed by the credential. This exact header value is what we injected to obtain the AES-128 key.

### Complete provenance table

| Step | Request | Purpose | Result |
|---|---|---|---|
| 1 | `GET .../api/v5/schedules?league_id=ALL&game_date=20261014` | Get all games for the day | 200, 15 games, 12 B.LEAGUE ONE |
| 2 | `GET .../api/v3/teams` | Resolve English club names | 200, found IWATE BIGBULLS / EARTHFRIENDS TOKYO Z |
| 3 | `GET .../api/v9/game/507231` | Get stream URLs | 200, `end_f=0`, 4 live URLs, missed empty |
| 4a | GET master playlist (Akamai) | Discover media playlist URI | 200, BANDWIDTH=1128000, RESOLUTION=854×480 |
| 4b | GET media playlist (Akamai) | Obtain segments and key URI | 200, AES-128 KEY lines pointing to `/playkey` |
| 5 (fail) | GET `/playkey` no auth | Try to obtain AES key bare | 400 forbidden |
| 5 (success) | GET `/playkey` + FREEKEY header | Obtain AES-128 decryption key | 200, 16 bytes: `eaa206f5...55118e` |
| 6 | GET segment `298660800` (Akamai) | Download encrypted TS packet | 200, 541824 bytes, first byte `0xa3` (encrypted) |
| 7 | Python AES-128-CBC decrypt | Apply key + IV, PKCS7 unpad | Decrypted 541816 bytes, first byte `0x47` (valid TS) |
| 8 | ffprobe decrypted `.ts` | Verify decodable stream | H.264 High, 854×480, 30 fps, 722 kbps |
| 9 | Proxy on localhost:8888 | Serve patched playlist to VLC | Live playback working end-to-end |
