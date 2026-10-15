"""B.LEAGUE men's basketball stream source.

Fetches schedules, resolves English club names, computes game phases,
resolves encrypted HLS streams, and enriches all data into a unified
game dictionary consumed by the FastAPI app.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
from cachetools import TTLCache

log = logging.getLogger("bleague")

# ---------------------------------------------------------------------------
# Constants — all hardcoded, matching nbl1-streams pattern
# ---------------------------------------------------------------------------

API_BASE      = "https://api.basketball.mb.softbank.jp/api"
CDN_BASE      = "https://basketlive-live-sni.akamaized.net/live/"

AUTH_HEADERS  = {
    "User-Agent":                "okhttp/4.9.3",
    "X-OS-TYPE":                 "ANDROID",
    "X-APP-VERSION":             "4.0.2",
    # Native app sends "- " (dash+space) via OkHttp which tolerates it.
    # httpx strictly validates per RFC 7230; use "-" only.
    "X-BASKETLIVE-ACCESSTOKEN":  "-",
}

FREEKEY_HEADER = "X-BASKETLIVE-FREEKEY"
FREEKEY_VALUE  = "Jc3_6cnTpWfZxoUPaBUgtHolu4p_xl72uyMkoRevA5Be6h_OshXi3ztp7PCOkA7Y"

VALID_LEAGUES = {"ALL", "BLEAGUE"}
B_LEAGUE_NAMES = {"B.LEAGUE ONE", "B.LEAGUE PREMIER"}

GAME_END_FLAGS = {"9": "before", "0": "during", "1": "over", "99": "cancel"}

# Live listing covers today and tomorrow (JST); completed looks back this many days.
LIVE_DAYS_AHEAD         = 1
COMPLETED_LOOKBACK_DAYS = 7

# Overseas opponents (pre-season / invitational) aren't in /api/v3/teams,
# so there is no upstream English name for them.
EXTRA_TEAM_NAMES_EN = {
    "10558": "SUWON KT SONICBOOM",
    "10576": "TELEKOM BASKETS BONN",
}

QUANTITY_MAP    = {"8M": "Full HD", "4M": "High", "1.5M": "Middle", "500K": "Low"}
# Best first: Full HD 1920x1080 (~6.1 Mbps), High 1280x720, Middle 854x480, Low 320x180.
QUALITY_PREFERENCE = ("Full HD", "High", "Middle", "Low")

# ---------------------------------------------------------------------------
# Caches & semaphore
# ---------------------------------------------------------------------------

season_cache  = TTLCache(maxsize=10, ttl=86400)     # never used but kept for NBL1 compat
data_cache    = TTLCache(maxsize=256, ttl=60)       # schedule / fixture data
detail_cache  = TTLCache(maxsize=512, ttl=60)       # game detail (stream URL lists)
team_cache    = TTLCache(maxsize=1, ttl=3600)       # team catalog (rarely changes)
master_cache  = TTLCache(maxsize=64, ttl=240)       # playkey master URLs (~4 min)

upstream_sem  = asyncio.Semaphore(20)               # throttle concurrent outbound calls
_resolve_lock = asyncio.Lock()                      # singleton lock for key caching

match_ref_map: dict[str, str] = {}                  # match_id → useful context (NBL1 compat)

client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UpstreamError(Exception):
    def __init__(self, status: int | None = None):
        self.status = status


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def startup() -> None:
    global client
    client = httpx.AsyncClient(headers=AUTH_HEADERS, timeout=httpx.Timeout(15.0))


async def shutdown() -> None:
    global client
    if client:
        await client.aclose()
        client = None


# ---------------------------------------------------------------------------
# Upstream helpers
# ---------------------------------------------------------------------------


async def fetch_json(url: str, cache: TTLCache | None = None) -> dict:
    """Generic JSON fetch with optional TTL caching."""
    if cache is not None and url in cache:
        return cache[url]
    async with upstream_sem:
        try:
            resp = await client.get(url, follow_redirects=True, timeout=httpx.Timeout(15.0))
        except httpx.HTTPError:
            raise UpstreamError()
        if resp.status_code != 200:
            raise UpstreamError(status=resp.status_code)
        data = resp.json()
    if cache is not None:
        cache[url] = data
    return data


async def fetch_playkey() -> bytes:
    """Obtain the 16-byte AES-128 license key via the FREEKEY credential."""
    async with _resolve_lock:
        if "playkey_key" in master_cache:
            return master_cache["playkey_key"]
        req = httpx.Request(
            "GET",
            "https://basketball.mb.softbank.jp/api/v1/playkey",
            headers={
                "User-Agent": "okhttp/4.9.3",
                FREEKEY_HEADER: FREEKEY_VALUE,
            },
        )
        try:
            resp = await client.send(req)
        except httpx.HTTPError:
            log.error("Playkey request failed")
            return b""
        assert resp.status_code == 200
        key = resp.content
        assert len(key) == 16, f"Expected 16-byte key, got {len(key)}"
        master_cache["playkey_key"] = key
        return key


# ---------------------------------------------------------------------------
# Schedule fetching
# ---------------------------------------------------------------------------


def _is_b_league(game: dict) -> bool:
    """Return True if the game belongs to a B.LEAGUE men's competition."""
    league = game.get("league_name", "")
    return league in B_LEAGUE_NAMES


async def fetch_day_schedule(date: str) -> list[dict]:
    """Fetch day-level schedule for game_date in yyyyMMdd format."""
    date_int = date.replace("-", "")
    url = f"{API_BASE}/v5/schedules?league_id=ALL&game_date={date_int}"
    try:
        data = await fetch_json(url, cache=data_cache)
    except UpstreamError:
        log.warning("Day schedule fetch failed for %s", date)
        return []
    return data.get("games", [])


async def fetch_month_schedule(month: str) -> list[dict]:
    """Fetch month-level schedule for game_month in yyyyMM format.
    
    Returns flattened list of games from all days in the month bucket.
    """
    url = f"{API_BASE}/v5/schedules/month?league_id=ALL&game_month={month}"
    try:
        data = await fetch_json(url, cache=data_cache)
    except UpstreamError:
        log.warning("Month schedule fetch failed for %s", month)
        return []
    games: list[dict] = []
    for day_bucket in data.get("games", []):
        games.extend(day_bucket.get("games", []))
    return games


async def fetch_all_days_schedules(year: int, month: int) -> list[dict]:
    """Fetch schedules for every day with games in a given month/year.
    
    Uses the 'schedule' array from a single day call to discover which
    dates have games, then fetches each day individually. This mirrors
    how the native app discovers the full calendar.
    """
    # Start with one day to discover available dates
    date_str = f"{year}{month:02d}01"
    base_games = await fetch_day_schedule(date_str)
    if not base_games:
        return []
    schedule_dates = set(base_games[0].get("game_date", ""))  # not reliable
    
    # Fallback: iterate every day in the month and collect games
    all_games: list[dict] = []
    days_in_month = _days_in_month(year, month)
    for day in range(1, days_in_month + 1):
        d = f"{year}{month:02d}{day:02d}"
        try:
            games = await fetch_day_schedule(d)
            all_games.extend(games)
        except Exception:
            continue  # skip bad dates
    
    return all_games


def _days_in_month(year: int, month: int) -> int:
    """Return number of days in year-month."""
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1
    first = datetime(next_y, next_m, 1)
    return (first - datetime(first.year, first.month, 1)).days


# ---------------------------------------------------------------------------
# Team catalog
# ---------------------------------------------------------------------------


async def fetch_team_catalog() -> dict[str, dict]:
    """Fetch all teams and build an index mapping team_id → {name_jp, name_en, name_es}.
    
    The response contains teams_info blocks across multiple league types.
    We filter to B.LEAGUE men-only types during index construction.
    """
    if "catalog_index" in team_cache:
        return team_cache["catalog_index"]
    
    url = f"{API_BASE}/v3/teams"
    try:
        data = await fetch_json(url)
    except UpstreamError:
        log.warning("Team catalog fetch failed")
        return {}
    
    index: dict[str, dict] = {}
    valid_league_substrings = {"B.LEAGUE ONE", "B.LEAGUE PREMIER", "B.LEAGUE NEXT"}
    for kind in data.get("teams_info", []):
        league_name = kind.get("team_kind_name", "")
        # Only include B.LEAGUE men's tiers
        if not any(s in league_name for s in valid_league_substrings):
            continue
        
        for conf in kind.get("items", []):
            for team in conf.get("teams", []):
                tid = team.get("team_id", "")
                if not tid:
                    continue
                # Normalize to string — catalog may return int, game rows use str
                index[str(tid)] = {
                    "name_jp":   team.get("team_name", ""),
                    "name_en":   team.get("team_name_e"),
                    "name_es":   team.get("team_name_es"),
                    "code":      team.get("team_name_s", ""),
                }
    
    team_cache["catalog_index"] = index
    return index


# ---------------------------------------------------------------------------
# Game detail
# ---------------------------------------------------------------------------


async def fetch_game_detail(game_id: str) -> dict | None:
    """Fetch /api/v9/game/{game_id} and return the parsed game dict.

    Cached briefly: the stream URLs are plain CDN paths (no expiring tokens),
    and listing endpoints would otherwise refetch every game on every request.
    """
    url = f"{API_BASE}/v9/game/{game_id}"
    try:
        data = await fetch_json(url, cache=detail_cache)
    except UpstreamError:
        log.debug("Game detail fetch failed for %s", game_id)
        return None
    game = data.get("game", {})
    if not game:
        return None
    return game


# ---------------------------------------------------------------------------
# Phase computation (from SCHEDULE.md Section 3)
# ---------------------------------------------------------------------------


def compute_phase(game_row: dict) -> str:
    """Compute game phase from game_end_f and timestamp fields.
    
    Returns one of: IN_FUTURE, IN_FUTURE_ONE_DAY, PLAYING,
    FINISHED_MISSED_MATCH, FINISHED, CANCELLED.
    
    Rules per decompiled GameItem.x():
    1. game_end_f=="99" → CANCELLED
    2. game_end_f=="1": missed if end < now < missedEnd, else FINISHED
    3. now < day → IN_FUTURE
    4. day < now < start → IN_FUTURE_ONE_DAY
    5. start < now < end → PLAYING
    6. end < now < missedEnd → FINISHED_MISSED_MATCH
    7. Otherwise → FINISHED
    
    Timestamps use GMT+9 equivalent clock; open-interval windows.
    Missing timestamps fall through to flag-only mapping.
    """
    flag = game_row.get("game_end_f", "9")
    
    live_start = game_row.get("live_start_date")
    live_end   = game_row.get("live_end_date")
    missed_end = game_row.get("missed_live_end_date")
    
    # If any critical timestamp is missing, fall back to flag-only mapping
    if not live_start or not live_end or not missed_end:
        return {
            "9":  "IN_FUTURE",
            "0":  "PLAYING",
            "1":  "FINISHED",
            "99": "CANCELLED",
        }.get(flag, "IN_FUTURE")
    
    # Parse timestamps and attach JST tzinfo so comparisons with now_jst work.
    JST = timezone(timedelta(hours=9))
    
    def parse_ts(ts_str: str | None) -> datetime | None:
        if not ts_str:
            return None
        try:
            dt = datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=JST)
        except ValueError:
            return None
    
    now_jst = datetime.now(JST)
    start = parse_ts(live_start)
    end   = parse_ts(live_end)
    miss  = parse_ts(missed_end)
    if not start or not end or not miss:
        return {
            "9":  "IN_FUTURE",
            "0":  "PLAYING",
            "1":  "FINISHED",
            "99": "CANCELLED",
        }.get(flag, "IN_FUTURE")
    
    # Day boundary: midnight on the live-start date (JST-aware)
    day_midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Evaluate in order (short-circuit)
    if flag == "99":
        return "CANCELLED"
    
    if flag == "1":
        # Ended — check missed window
        if end < now_jst < miss:
            return "FINISHED_MISSED_MATCH"
        return "FINISHED"
    
    if now_jst < day_midnight:
        return "IN_FUTURE"
    if day_midnight < now_jst < start:
        return "IN_FUTURE_ONE_DAY"
    if start < now_jst < end:
        return "PLAYING"
    if end < now_jst < miss:
        return "FINISHED_MISSED_MATCH"
    
    return "FINISHED"


# ---------------------------------------------------------------------------
# Stream resolution
# ---------------------------------------------------------------------------


LIVE_PHASES = {"PLAYING", "FINISHED_MISSED_MATCH", "IN_FUTURE", "IN_FUTURE_ONE_DAY"}
COMPLETED_PHASES = {"FINISHED"}


def select_stream_list(phase: str, game_end_f: str, 
                        live_urls: list | None, missed_urls: list | None) -> str | None:
    """Select which URL list to use based on playback rules from SCHEDULE.md.
    
    Returns "live_urls", "missed_urls", or None.
    """
    if game_end_f == "1" and missed_urls:
        return "missed_urls"
    if game_end_f == "1" and not missed_urls and live_urls:
        return "live_urls"
    if phase == "PLAYING":
        return "live_urls"
    if phase == "FINISHED_MISSED_MATCH" and game_end_f != "1":
        return "live_urls"
    return None


def pick_quality(urls: list | None) -> str | None:
    """From a list of quality objects, pick the highest quality available."""
    if not urls:
        return None
    
    by_label = {entry.get("quality"): entry.get("url") for entry in urls}
    for label in QUALITY_PREFERENCE:
        if by_label.get(label):
            return by_label[label]
    
    # Fallback: unknown labels, use first available
    return urls[0].get("url") if urls else None


async def resolve_stream_for_match(match_id: str) -> str | None:
    """Resolve CDN playlist URL for a match on demand (never cached).
    
    Flow:
      1. Fetch game detail → get live_urls / missed_urls
      2. Select appropriate list
      3. Pick the best available quality
      4. Return CDN URL string
    """
    detail = await fetch_game_detail(match_id)
    if not detail:
        return None
    
    live_urls   = detail.get("live_urls")
    missed_urls = detail.get("missed_urls")
    flag        = detail.get("game_end_f", "9")
    
    # Reconstruct phase-like info from flag + timestamps
    phase = compute_phase(detail)
    
    list_choice = select_stream_list(phase, flag, live_urls, missed_urls)
    if not list_choice:
        return None
    
    urls_list = live_urls if list_choice == "live_urls" else missed_urls
    return pick_quality(urls_list)


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def enrich_game(game_row: dict, team_index: dict, phase: str) -> dict:
    """Build canonical game dict from a schedule row + resolved team data + computed phase.
    
    Fields populated:
      match_id, international_ref, game_end_f, computed_phase,
      start_time_utc, start_time_local, round, fixture_type,
      season_name, venue, conference, home_team, away_team,
      stream_url, videos
    """
    mid         = game_row.get("game_id", "")
    flag        = game_row.get("game_end_f", "9")
    home_id     = game_row.get("home_team_id", "")
    away_id     = game_row.get("away_team_id", "")
    venue_ref   = game_row.get("stadium_name", "")
    
    # Resolve teams
    home_idx = team_index.get(home_id, {})
    away_idx = team_index.get(away_id, {})
    home_en  = home_idx.get("name_en") or EXTRA_TEAM_NAMES_EN.get(home_id or "")
    away_en  = away_idx.get("name_en") or EXTRA_TEAM_NAMES_EN.get(away_id or "")
    
    # Convert game_date + game_time to UTC datetime string
    gdate = game_row.get("game_date", "")           # "2026-10-14"
    gtime = game_row.get("game_time", "")           # "19:05:00"
    local_dt_str = f"{gdate} {gtime}"               # JST
    
    utc_dt_str = _jst_to_utc_iso(local_dt_str) if local_dt_str else ""
    time_local = gtime[:5] if gtime else ""         # "HH:MM"
    
    league_name = game_row.get("league_name", "B.LEAGUE")
    
    return {
        "match_id":          mid,
        "international_ref": mid,                     # B.LEEGUE uses game_id as ref
        "game_end_f":        flag,
        "computed_phase":    phase,
        "start_time_utc":    utc_dt_str,
        "start_time_local":  time_local,
        "round":             game_row.get("occasion_no"),            # 節 (matchweek)
        "fixture_type":      game_row.get("game_kind_name") or "",   # e.g. B.LEAGUE PRE-SEASON GAME
        "title":             game_row.get("title"),                  # set on rows with no teams (shows, tours)
        "season_name":       league_name,
        "venue":             venue_ref,
        "conference":        league_name,
        "home_team": {
            "id":        home_id,
            "name_jp":   home_idx.get("name_jp", game_row.get("home_team_name", "")),
            "name_en":   home_en,
            "name_es":   home_idx.get("name_es"),
            "score":     None,                       # no score on schedule rows
            "logo_url":  None,
        },
        "away_team": {
            "id":        away_id,
            "name_jp":   away_idx.get("name_jp", game_row.get("away_team_name", "")),
            "name_en":   away_en,
            "name_es":   away_idx.get("name_es"),
            "score":     None,
            "logo_url":  None,
        },
        "live_urls":       [],
        "missed_urls":     [],
        "stream_url":      None,
    }


def _jst_to_utc_iso(jst_str: str) -> str:
    """Convert '2026-10-14 19:05:00' (JST) → ISO-UTC string."""
    dt_jst = datetime.strptime(jst_str.strip(), "%Y-%m-%d %H:%M:%S")
    dt_jst = dt_jst.replace(tzinfo=timezone(timedelta(hours=9)))
    dt_utc = dt_jst.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------


LIVE_LIST_PHASES = {"PLAYING", "IN_FUTURE_ONE_DAY", "IN_FUTURE"}
COMPLETED_LIST_PHASES = {"FINISHED_MISSED_MATCH", "FINISHED"}


def _jst_today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def _in_date_range(game_row: dict, first, last) -> bool:
    try:
        d = datetime.strptime(game_row.get("game_date") or "", "%Y-%m-%d").date()
    except ValueError:
        return False
    return first <= d <= last


async def _fetch_months(first, last) -> list[dict]:
    """Month schedules covering first..last (dates), deduplicated by game_id."""
    months: list[str] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        months.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    results = await asyncio.gather(*(fetch_month_schedule(mo) for mo in months))
    seen: set[str] = set()
    games: list[dict] = []
    for rows in results:
        for g in rows:
            gid = g.get("game_id")
            if gid and gid not in seen:
                seen.add(gid)
                games.append(g)
    return games


async def process_live_games(all_games: list[dict], team_index: dict) -> list[dict]:
    """Enrich upcoming and in-progress games; resolve streams for PLAYING ones."""
    enriched: list[dict] = []
    for game_row in all_games:
        if not _is_b_league(game_row):
            continue
        phase = compute_phase(game_row)
        if phase not in LIVE_LIST_PHASES:
            continue
        enriched.append(enrich_game(game_row, team_index, phase))
    
    playing = [g for g in enriched if g["computed_phase"] == "PLAYING"]
    urls = await asyncio.gather(*(resolve_stream_for_match(g["match_id"]) for g in playing))
    for game, url in zip(playing, urls):
        game["stream_url"] = url
    
    enriched.sort(key=lambda g: g.get("start_time_utc", ""))
    return enriched


async def _attach_catchup(game: dict) -> None:
    detail = await fetch_game_detail(game["match_id"])
    if not detail:
        return
    missed = detail.get("missed_urls") or []
    live   = detail.get("live_urls") or []
    # Legacy fallback: no catch-up list, but live URL still exists
    urls, title = (missed, "Catch-Up") if missed else (live, "Replay")
    url = pick_quality(urls)
    if url:
        game["stream_url"] = url
        game["videos"] = {
            "full_game": {
                "video_id":   game["match_id"],
                "title":      title,
                "stream_url": url,
            }
        }


async def process_completed_games(all_games: list[dict], team_index: dict) -> list[dict]:
    """Enrich finished games and attach catch-up URLs (fetched concurrently)."""
    enriched: list[dict] = []
    for game_row in all_games:
        if not _is_b_league(game_row):
            continue
        phase = compute_phase(game_row)
        if phase not in COMPLETED_LIST_PHASES:
            continue
        enriched.append(enrich_game(game_row, team_index, phase))
    
    await asyncio.gather(*(_attach_catchup(g) for g in enriched))
    
    enriched.sort(key=lambda g: g.get("start_time_utc", ""), reverse=True)
    return enriched


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------


async def fetch_live_games() -> list[dict]:
    """Upcoming and in-progress B.LEAGUE games for today and tomorrow (JST)."""
    today = _jst_today()
    last = today + timedelta(days=LIVE_DAYS_AHEAD)
    games, team_index = await asyncio.gather(_fetch_months(today, last), fetch_team_catalog())
    games = [g for g in games if _in_date_range(g, today, last)]
    return await process_live_games(games, team_index)


async def fetch_completed_games() -> list[dict]:
    """B.LEAGUE games finished in the last COMPLETED_LOOKBACK_DAYS days (JST)."""
    today = _jst_today()
    first = today - timedelta(days=COMPLETED_LOOKBACK_DAYS)
    games, team_index = await asyncio.gather(_fetch_months(first, today), fetch_team_catalog())
    games = [g for g in games if _in_date_range(g, first, today)]
    return await process_completed_games(games, team_index)
