"""
Live race tracking via the OpenF1 API.

Polls OpenF1 endpoints for real-time race data (positions, pit stops,
tyre stints, race control messages) and maintains a module-level state
dict that the SSE endpoint streams to connected frontends.

Phase 2 additions: after each polling cycle, the engine recalculates
optimal strategies for all drivers whenever a pit event or SC status
change is detected.  Results are cached in _race_state["strategies"]
keyed by driver number, and the frontend filters to the selected team.

Key design decisions:
- Module-level functions + shared state dict (no classes) — matches codebase convention
- Single uvicorn worker required (--workers 1) because _race_state is in-process memory
- Full state snapshots over SSE (no partial updates) — eliminates merge bugs
- One race at a time — appropriate for a fan tool on a home Kubernetes lab
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from f1_strat.strategy import StrategyEngine

logger = logging.getLogger(__name__)

# Strategy engine — shared instance for mid-race recalculation.
# Initialized once because StrategyEngine.__init__ sets up the FastF1 cache.
_strategy_engine = StrategyEngine()

# Recalculation coalescing flags.  _is_calculating prevents overlapping
# recalculations.  _needs_recalc queues a follow-up recalculation if a
# trigger fires while a calculation is already running.
_is_calculating: bool = False
_needs_recalc: bool = False


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget background tasks.

    Without this callback, exceptions from create_task() are silently
    swallowed and only produce a warning when the task is garbage-collected.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task failed: %s", exc, exc_info=exc)

# ---------------------------------------------------------------------------
# OpenF1 API base URL
# ---------------------------------------------------------------------------
_OPENF1_BASE = "https://api.openf1.org/v1"

# ---------------------------------------------------------------------------
# OpenF1 OAuth2 credentials (sponsor tier)
# ---------------------------------------------------------------------------
# Read from environment variables — when not set, all requests go through
# the free tier as before (no auth headers, historical data only).
_OPENF1_USERNAME: str | None = os.getenv("OPENF1_USERNAME")
_OPENF1_PASSWORD: str | None = os.getenv("OPENF1_PASSWORD")

# Cached bearer token and its expiry (monotonic clock, immune to NTP jumps).
# _token_expires_at starts at 0.0 so the first request triggers authentication.
_token_value: str | None = None
_token_expires_at: float = 0.0

# Refresh the token 60 seconds before it actually expires.  This margin
# prevents the token from expiring between our check and the server receiving
# the request — especially important over slow connections.
_TOKEN_REFRESH_MARGIN_S: float = 60.0

# ---------------------------------------------------------------------------
# Shared HTTP client — lazy-initialized, reused across all polling calls
# ---------------------------------------------------------------------------
_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """Lazy-init a shared async HTTP client.

    Uses a 30-second timeout because OpenF1 historical data fetches can be
    slow, and httpx's default 5s would cause spurious timeouts.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=_OPENF1_BASE,
            timeout=httpx.Timeout(30.0),
        )
    return _http_client


async def close_client() -> None:
    """Shutdown hook — close the httpx connection pool.

    Called by FastAPI's shutdown event handler in api.py so we don't
    leak TCP connections when the server stops.  Also clears any cached
    auth token so a restart forces re-authentication.
    """
    global _http_client, _token_value, _token_expires_at
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
    _token_value = None
    _token_expires_at = 0.0


# ---------------------------------------------------------------------------
# OpenF1 OAuth2 token exchange
# ---------------------------------------------------------------------------

async def _authenticate(
    _auth_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Exchange username/password for a bearer token.

    POSTs credentials to the OpenF1 token endpoint and caches the result.
    The token is valid for 1 hour; we subtract _TOKEN_REFRESH_MARGIN_S so
    we refresh before it actually expires.

    Args:
        _auth_client: Optional injected client for testing.  Production
                      code leaves this as None to create a fresh client
                      (the token endpoint is at the API root, not /v1).

    Returns:
        The bearer token string, or None if authentication failed.
    """
    global _token_value, _token_expires_at

    if not _OPENF1_USERNAME or not _OPENF1_PASSWORD:
        return None

    # Use a separate client for the token endpoint because it lives at the
    # API root (https://api.openf1.org/token), not under /v1.
    client = _auth_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        resp = await client.post(
            "https://api.openf1.org/token",
            data={"username": _OPENF1_USERNAME, "password": _OPENF1_PASSWORD},
        )

        if resp.status_code != 200:
            logger.warning(
                "OpenF1 token exchange failed (%d) — falling back to free tier",
                resp.status_code,
            )
            _token_value = None
            _token_expires_at = 0.0
            return None

        body = resp.json()
        _token_value = body.get("access_token")
        # Token is valid for 1 hour (3600s).  Subtract the refresh margin
        # so we re-authenticate before the server rejects us.
        _token_expires_at = time.monotonic() + 3600 - _TOKEN_REFRESH_MARGIN_S
        logger.info("OpenF1 authenticated — token cached for ~59 minutes")
        return _token_value

    except Exception as e:
        logger.warning("OpenF1 token exchange error: %s — falling back to free tier", e)
        _token_value = None
        _token_expires_at = 0.0
        return None
    finally:
        # Only close the client if we created it (don't close injected mocks)
        if _auth_client is None:
            await client.aclose()


async def _get_auth_headers() -> dict[str, str]:
    """Return auth headers for an OpenF1 API request.

    - No credentials configured → returns {} (free tier, no behavior change)
    - Valid cached token → returns {"Authorization": "Bearer <token>"}
    - Expired/missing token → calls _authenticate() first, then returns header

    Called per-request (not baked into the httpx client) because tokens
    expire hourly and we don't want to tear down the connection pool.
    """
    if not _OPENF1_USERNAME or not _OPENF1_PASSWORD:
        # Free tier — no auth needed
        return {}

    # Check if we have a valid cached token
    if _token_value and time.monotonic() < _token_expires_at:
        return {"Authorization": f"Bearer {_token_value}"}

    # Token missing or expired — authenticate
    token = await _authenticate()
    if token:
        return {"Authorization": f"Bearer {token}"}

    # Auth failed — fall back to free tier (no header)
    return {}


# ---------------------------------------------------------------------------
# OpenF1 fetching — handles rate limits and errors
# ---------------------------------------------------------------------------

# Backoff state for rate limiting
_backoff_seconds: float = 0.0


async def fetch_openf1(endpoint: str, params: dict | None = None) -> list[dict]:
    """Fetch data from an OpenF1 endpoint.

    Args:
        endpoint: API path like "/race_control" (no base URL prefix).
        params: Query parameters as a dict.  OpenF1 uses special keys
                like "date>" for greater-than filters, which work naturally
                as dict keys.

    Returns:
        List of dicts from the JSON response, or empty list on error.

    Handles:
        - 429 (rate limit): respects Retry-After, backs off progressively
        - 401/403 (auth): one token refresh + retry, then stops polling
        - 500/timeout: logs warning, returns empty list (keeps last state)
    """
    global _backoff_seconds, _token_value, _token_expires_at

    client = await _get_client()
    headers = await _get_auth_headers()

    try:
        resp = await client.get(endpoint, params=params, headers=headers)

        if resp.status_code == 429:
            # Rate limited — back off progressively
            retry_after = int(resp.headers.get("Retry-After", "30"))
            _backoff_seconds = max(retry_after, _backoff_seconds * 2 or 30)
            logger.warning(
                "OpenF1 rate limited (429), backing off %.0fs", _backoff_seconds
            )
            return []

        if resp.status_code in (401, 403):
            if _OPENF1_USERNAME and _OPENF1_PASSWORD:
                # Credentials are configured — token may have expired mid-session.
                # Clear the cached token and try once with a fresh one.
                logger.warning(
                    "OpenF1 %d with credentials — refreshing token and retrying",
                    resp.status_code,
                )
                _token_value = None
                _token_expires_at = 0.0
                fresh_headers = await _get_auth_headers()
                if fresh_headers:
                    retry_resp = await client.get(
                        endpoint, params=params, headers=fresh_headers,
                    )
                    if retry_resp.status_code < 400:
                        _backoff_seconds = 0.0
                        retry_resp.raise_for_status()
                        return retry_resp.json()

            # Either no credentials, or retry also failed — stop polling
            logger.error("OpenF1 auth error (%d) — stopping polling", resp.status_code)
            await stop_polling()
            return []

        if resp.status_code >= 500:
            logger.warning("OpenF1 server error (%d) on %s", resp.status_code, endpoint)
            return []

        # Success — reset backoff
        _backoff_seconds = 0.0
        resp.raise_for_status()
        return resp.json()

    except httpx.TimeoutException:
        logger.warning("OpenF1 timeout on %s", endpoint)
        return []
    except httpx.HTTPError as e:
        logger.warning("OpenF1 HTTP error on %s: %s", endpoint, e)
        return []


# ---------------------------------------------------------------------------
# Race state — the single source of truth, read by SSE endpoint
# ---------------------------------------------------------------------------

def _empty_state() -> dict:
    """Return a fresh empty race state dict."""
    return {
        "session_key": None,
        "current_lap": 0,
        "total_laps": 0,
        "is_safety_car": False,
        "last_race_control_message": "",
        "drivers": {},              # keyed by driver_number (int)
        "race_control_log": [],     # last 50 messages
        "pit_log": [],              # all pit events this session
        "strategies": {},           # keyed by driver_number (int) → top 3 strategies
        "last_updated": None,       # ISO timestamp
        "connected_to_openf1": False,
        "polling_active": False,
        "year": None,               # stored for recalculation
        "grand_prix": None,         # stored for recalculation
    }


_race_state: dict = _empty_state()

# Track connected SSE clients so we can auto-stop polling
_sse_client_count: int = 0

# Condition that wakes SSE generators immediately when state changes.
# Uses notify_all() so all waiting generators wake up at once — unlike
# Event.set()/clear(), Condition.notify_all() is designed for this
# producer/multiple-consumer pattern and has no timing edge cases.
_state_changed: asyncio.Condition = asyncio.Condition()

# ---------------------------------------------------------------------------
# State update functions — called by the polling loop
# ---------------------------------------------------------------------------

def _update_from_race_control(messages: list[dict]) -> None:
    """Process race control messages — SC/VSC detection and log.

    OpenF1 race_control messages have:
      - message: human-readable text (e.g., "SAFETY CAR DEPLOYED")
      - category: "Flag", "Other", "Drs", "SafetyCar", etc.
      - flag: "GREEN", "YELLOW", etc. (or null)
      - lap_number: which lap this occurred on
      - date: ISO timestamp
    """
    if not messages:
        return

    for msg in messages:
        text = msg.get("message", "")

        # SC/VSC detection — check message text for deployment/ending keywords
        if "SAFETY CAR DEPLOYED" in text or "VIRTUAL SAFETY CAR DEPLOYED" in text:
            _race_state["is_safety_car"] = True
            logger.info("Safety car detected: %s", text)
        elif "SAFETY CAR IN THIS LAP" in text or "VSC ENDING" in text:
            _race_state["is_safety_car"] = False
            logger.info("Safety car ending: %s", text)
        elif msg.get("flag") == "GREEN" and _race_state["is_safety_car"]:
            # GREEN flag after SC period ends it
            _race_state["is_safety_car"] = False

        # Add to the race control log (keep last 50 messages)
        _race_state["race_control_log"].append({
            "lap": msg.get("lap_number"),
            "message": text,
            "category": msg.get("category"),
            "flag": msg.get("flag"),
            "date": msg.get("date"),
        })

    # Trim to last 50 messages
    _race_state["race_control_log"] = _race_state["race_control_log"][-50:]
    _race_state["last_race_control_message"] = messages[-1].get("message", "")


def _update_from_pits(pit_events: list[dict]) -> None:
    """Record pit stop events.

    OpenF1 pit data has:
      - driver_number, lap_number, pit_duration (seconds), date
    """
    for pit in pit_events:
        entry = {
            "driver_number": pit.get("driver_number"),
            "lap": pit.get("lap_number"),
            "duration_s": pit.get("pit_duration"),
            "date": pit.get("date"),
        }
        # Avoid duplicates — check if we already have this pit stop
        existing = [
            p for p in _race_state["pit_log"]
            if p["driver_number"] == entry["driver_number"]
            and p["lap"] == entry["lap"]
        ]
        if not existing:
            _race_state["pit_log"].append(entry)
            logger.info(
                "Pit stop: driver %s, lap %s, %.1fs",
                entry["driver_number"], entry["lap"], entry["duration_s"] or 0,
            )


def _update_from_stints(stints: list[dict]) -> None:
    """Update driver compound and tyre age from stint data.

    OpenF1 stint data has:
      - driver_number, compound, tyre_age_at_start, lap_start, lap_end, stint_number
    """
    for stint in stints:
        driver_num = stint.get("driver_number")
        if driver_num is None:
            continue

        driver = _race_state["drivers"].get(driver_num)
        if driver is None:
            continue

        compound = stint.get("compound", "UNKNOWN")
        driver["current_compound"] = compound

        # Calculate tyre age: current_lap - lap_start + tyre_age_at_start
        lap_start = stint.get("lap_start", 0)
        age_at_start = stint.get("tyre_age_at_start", 0)
        driver["tyre_age"] = max(0, _race_state["current_lap"] - lap_start + age_at_start)

        # Track all compounds used (for FIA diversity check in Phase 2)
        if compound != "UNKNOWN" and compound not in driver["compounds_used"]:
            driver["compounds_used"].append(compound)

        # Count stops from stint number (stint 1 = 0 stops, stint 2 = 1 stop, etc.)
        stint_number = stint.get("stint_number", 1)
        driver["stops_completed"] = max(driver["stops_completed"], stint_number - 1)


def _update_from_positions(positions: list[dict]) -> None:
    """Update driver positions from position data.

    OpenF1 position data has:
      - driver_number, position, date
    We take the latest position for each driver.
    """
    # Build a map of latest position per driver
    latest: dict[int, int] = {}
    for pos in positions:
        driver_num = pos.get("driver_number")
        position = pos.get("position")
        if driver_num is not None and position is not None:
            latest[driver_num] = position

    # Apply to race state
    for driver_num, position in latest.items():
        driver = _race_state["drivers"].get(driver_num)
        if driver is not None:
            driver["position"] = position


def _update_from_intervals(intervals: list[dict]) -> None:
    """Update gap-to-leader and interval from interval data.

    OpenF1 interval data has:
      - driver_number, gap_to_leader, interval, date
    We take the latest values for each driver.
    """
    # Build a map of latest gap/interval per driver
    latest: dict[int, dict] = {}
    for iv in intervals:
        driver_num = iv.get("driver_number")
        if driver_num is not None:
            latest[driver_num] = {
                "gap_to_leader": iv.get("gap_to_leader"),
                "interval": iv.get("interval"),
            }

    # Apply to race state
    for driver_num, data in latest.items():
        driver = _race_state["drivers"].get(driver_num)
        if driver is not None:
            if data["gap_to_leader"] is not None:
                driver["gap_to_leader"] = data["gap_to_leader"]
            if data["interval"] is not None:
                driver["interval"] = data["interval"]


def _update_current_lap() -> None:
    """Derive current_lap from the latest stint data.

    Uses the maximum lap_end across all stints, falling back to
    pit stop lap numbers.
    """
    max_lap = 0

    # Check stints for the latest lap_end
    for driver in _race_state["drivers"].values():
        # We don't store lap_end per driver — use tyre_age + last known lap_start
        # But simpler: check pit_log for latest lap numbers
        pass

    # Use pit log as a secondary signal
    for pit in _race_state["pit_log"]:
        lap = pit.get("lap")
        if lap is not None and lap > max_lap:
            max_lap = lap

    if max_lap > _race_state["current_lap"]:
        _race_state["current_lap"] = max_lap


# ---------------------------------------------------------------------------
# Strategy recalculation — triggered by pit events or SC changes
# ---------------------------------------------------------------------------

async def _maybe_recalculate() -> None:
    """Recalculate strategies for all drivers if conditions changed.

    Uses try/finally to ensure _is_calculating is always cleared — if
    calculate_remaining() raises, a stuck flag would freeze all future
    recalculations.

    The _needs_recalc / _is_calculating pattern coalesces multiple triggers
    that fire in quick succession (e.g., multiple pit stops on the same lap)
    into a single recalculation pass.
    """
    global _is_calculating, _needs_recalc

    if _is_calculating:
        # Already running — queue a follow-up recalculation
        _needs_recalc = True
        return

    _is_calculating = True
    try:
        year = _race_state.get("year")
        grand_prix = _race_state.get("grand_prix")
        current_lap = _race_state.get("current_lap", 0)
        total_laps = _race_state.get("total_laps", 0)

        if not year or not grand_prix or current_lap < 1 or total_laps < 1:
            return

        strategies_by_driver: dict[int, list] = {}

        for driver_num, driver in _race_state["drivers"].items():
            compound = driver.get("current_compound", "UNKNOWN")
            if compound == "UNKNOWN":
                continue

            try:
                result = _strategy_engine.calculate_remaining(
                    year=year,
                    grand_prix=grand_prix,
                    current_lap=current_lap,
                    race_laps=total_laps,
                    current_compound=compound,
                    tyre_age=driver.get("tyre_age", 0),
                    stops_completed=driver.get("stops_completed", 0),
                    compounds_used=driver.get("compounds_used", []),
                    max_stops=2,
                )
                # Store the top 3 strategies for this driver
                top = result.get("strategies", [])[:3]
                strategies_by_driver[driver_num] = top
            except Exception as e:
                logger.warning(
                    "Strategy recalc failed for driver %s: %s",
                    driver_num, e,
                )

        _race_state["strategies"] = strategies_by_driver
        _race_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        # Wake SSE clients so they get the new strategies immediately.
        # This is important when recalculation runs as a background task
        # (create_task) — without this, clients would wait up to 15s.
        async with _state_changed:
            _state_changed.notify_all()
        logger.info(
            "Recalculated strategies for %d drivers (lap %d/%d)",
            len(strategies_by_driver), current_lap, total_laps,
        )

    finally:
        _is_calculating = False

        # If another trigger arrived while we were calculating, run again.
        # Clear the flag BEFORE the recursive call to avoid missing a
        # trigger that arrives during the second calculation.
        if _needs_recalc:
            _needs_recalc = False
            await _maybe_recalculate()


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

# The background polling task — None when not polling
_polling_task: asyncio.Task | None = None

# Timestamps for incremental fetching (only get new data since last poll)
_last_timestamps: dict[str, str | None] = {
    "race_control": None,
    "pit": None,
    "position": None,
    "intervals": None,
}


def _make_since_params(session_key: int, endpoint_key: str) -> dict:
    """Build query params for an OpenF1 endpoint, including the 'date>' filter
    for incremental polling (only fetch data newer than last poll)."""
    params: dict = {"session_key": session_key}
    since = _last_timestamps.get(endpoint_key)
    if since is not None:
        params["date>"] = since
    return params


def _track_latest_timestamp(data: list[dict], endpoint_key: str) -> None:
    """Record the latest 'date' from a batch of OpenF1 records so the next
    poll only fetches newer data."""
    if not data:
        return
    dates = [d.get("date") for d in data if d.get("date")]
    if dates:
        _last_timestamps[endpoint_key] = max(dates)


async def start_polling(
    session_key: int,
    total_laps: int,
    year: int | None = None,
    grand_prix: str | None = None,
) -> None:
    """Start the background polling loop for a race session.

    Polls all 5 OpenF1 endpoints in round-robin at ~8s intervals.
    Updates _race_state on each cycle.

    Args:
        session_key: OpenF1 session identifier (integer).
        total_laps: Total laps in the race (for completion detection).
    """
    global _polling_task

    # If already polling the same session, it's a no-op
    if _polling_task is not None and not _polling_task.done():
        if _race_state["session_key"] == session_key:
            logger.info("Already polling session %d — no-op", session_key)
            return
        # Different session — stop the old one first
        await stop_polling()

    # Reset state for the new session
    _race_state.update(_empty_state())
    _race_state["session_key"] = session_key
    _race_state["total_laps"] = total_laps
    _race_state["polling_active"] = True
    # Store year/grand_prix for mid-race strategy recalculation.
    # These are needed by calculate_remaining() to load practice deg data.
    _race_state["year"] = year
    _race_state["grand_prix"] = grand_prix

    # Reset incremental timestamps
    for key in _last_timestamps:
        _last_timestamps[key] = None

    # Load drivers for this session
    drivers_data = await fetch_openf1("/drivers", {"session_key": session_key})
    for d in drivers_data:
        driver_num = d.get("driver_number")
        if driver_num is not None:
            _race_state["drivers"][driver_num] = {
                "driver_number": driver_num,
                "abbreviation": d.get("name_acronym", ""),
                "full_name": d.get("full_name", ""),
                "team": d.get("team_name", ""),
                "team_color": f"#{d.get('team_colour', '666666')}",
                "position": 0,
                "current_compound": "UNKNOWN",
                "tyre_age": 0,
                "compounds_used": [],
                "stops_completed": 0,
                "last_lap_time": None,
                "gap_to_leader": 0.0,
                "interval": 0.0,
            }

    logger.info(
        "Starting polling for session %d (%d drivers, %d laps)",
        session_key, len(_race_state["drivers"]), total_laps,
    )

    _race_state["connected_to_openf1"] = True

    # Launch the polling loop as a background task
    _polling_task = asyncio.create_task(_poll_loop(session_key))


async def _poll_loop(session_key: int) -> None:
    """The actual polling loop — runs until stopped, race ends, or no clients.

    Fetches non-stint endpoints concurrently with asyncio.gather(), then
    does a single full stints fetch (needed for lap_end values that
    incremental fetching doesn't provide).

    Strategy recalculation runs as a background task (create_task) so it
    never blocks the polling loop — SSE clients see position/tyre updates
    immediately while strategies compute in the background.

    The cycle interval depends on whether sponsor credentials are configured:
      - Sponsor tier (credentials set): ~4s cycles for near-real-time updates
      - Free tier (no credentials):     ~8s cycles to stay within rate limits
    """
    # Non-stint endpoints to poll concurrently.  Stints are fetched
    # separately as a full (non-incremental) fetch because we need
    # lap_end values that only appear when the stint is complete.
    endpoints = [
        ("race_control", "/race_control", _update_from_race_control),
        ("pit", "/pit", _update_from_pits),
        ("position", "/position", _update_from_positions),
        ("intervals", "/intervals", _update_from_intervals),
    ]

    async def _fetch_endpoint(key: str, path: str) -> tuple[str, list[dict]]:
        """Fetch a single OpenF1 endpoint with incremental since-params."""
        params = _make_since_params(session_key, key)
        data = await fetch_openf1(path, params)
        return key, data

    idle_since: float | None = None

    while _race_state["polling_active"]:
        cycle_start = asyncio.get_event_loop().time()

        # Check for rate limit backoff
        if _backoff_seconds > 0:
            logger.info("Rate limit backoff: waiting %.0fs", _backoff_seconds)
            await asyncio.sleep(_backoff_seconds)

        # Track whether any strategy-relevant changes occurred this cycle.
        # Pit events and SC status changes both warrant a recalculation
        # because they affect the optimal remaining strategy.
        sc_before = _race_state["is_safety_car"]
        pit_count_before = len(_race_state["pit_log"])

        # Fetch all non-stint endpoints concurrently — each endpoint is
        # independent, so there's no reason to wait for one before starting
        # the next.  This turns 4 × ~500ms sequential into 1 × ~500ms.
        # return_exceptions=True ensures one failed endpoint doesn't cancel
        # the others — each result is processed independently.
        results = await asyncio.gather(
            *[_fetch_endpoint(key, path) for key, path, _ in endpoints],
            return_exceptions=True,
        )

        # Apply results — gather preserves input order, so we zip against
        # the original endpoints list to get each result's updater.
        for (_, _, updater), result in zip(endpoints, results):
            if isinstance(result, Exception):
                logger.warning("Endpoint fetch failed: %s", result)
                continue
            key, data = result
            _track_latest_timestamp(data, key)
            if data:
                updater(data)

        # Full stints fetch — provides lap_end values for current lap
        # detection AND compound/tyre data for driver state.  Fetched
        # separately (not incrementally) because lap_end only appears
        # when a stint is complete.
        all_stints = await fetch_openf1("/stints", {"session_key": session_key})
        if all_stints:
            # Find the max lap_end across all drivers for current lap
            max_lap = 0
            for s in all_stints:
                lap_end = s.get("lap_end")
                if lap_end is not None and lap_end > max_lap:
                    max_lap = lap_end
            if max_lap > _race_state["current_lap"]:
                _race_state["current_lap"] = max_lap

            # Update compound and tyre age for all drivers
            _update_from_stints(all_stints)

        # Mark the state as updated and wake SSE clients immediately
        _race_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        async with _state_changed:
            _state_changed.notify_all()

        # Trigger strategy recalculation if something strategy-relevant changed:
        # 1. New pit events (a driver pitted → different compound/tyre age)
        # 2. SC status change (SC deployed or ended → affects optimal strategy)
        # 3. First cycle (no strategies calculated yet)
        # Runs as a background task so the polling loop continues immediately.
        # The _is_calculating / _needs_recalc flags prevent overlapping runs.
        sc_changed = _race_state["is_safety_car"] != sc_before
        new_pits = len(_race_state["pit_log"]) > pit_count_before
        no_strategies_yet = not _race_state.get("strategies")

        if (sc_changed or new_pits or no_strategies_yet) and _race_state["current_lap"] > 0:
            task = asyncio.create_task(_maybe_recalculate())
            task.add_done_callback(_log_task_exception)

        # Check if race is finished
        if (
            _race_state["total_laps"] > 0
            and _race_state["current_lap"] >= _race_state["total_laps"]
        ):
            logger.info("Race finished (lap %d/%d) — stopping polling",
                        _race_state["current_lap"], _race_state["total_laps"])
            _race_state["polling_active"] = False
            break

        # Auto-stop if no SSE clients for 5 minutes
        if _sse_client_count == 0:
            if idle_since is None:
                idle_since = asyncio.get_event_loop().time()
            elif asyncio.get_event_loop().time() - idle_since > 300:
                logger.info("No SSE clients for 5 minutes — stopping polling")
                _race_state["polling_active"] = False
                break
        else:
            idle_since = None

        # Wait for the remainder of the poll cycle.  Sponsor tier gets
        # faster updates (4s) since they have higher rate limits.
        cycle_target = 4.0 if (_OPENF1_USERNAME and _OPENF1_PASSWORD) else 8.0
        elapsed = asyncio.get_event_loop().time() - cycle_start
        sleep_time = max(0, cycle_target - elapsed)
        await asyncio.sleep(sleep_time)

    _race_state["connected_to_openf1"] = False
    logger.info("Polling loop ended for session %d", session_key)


async def stop_polling() -> None:
    """Stop the polling loop if it's running."""
    global _polling_task

    _race_state["polling_active"] = False

    if _polling_task is not None and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        logger.info("Polling task cancelled")

    _polling_task = None


# ---------------------------------------------------------------------------
# Session key resolution
# ---------------------------------------------------------------------------

async def resolve_session_key(
    year: int, country_name: str, session_name: str = "Race"
) -> int | None:
    """Resolve a year + country to an OpenF1 session_key.

    Queries the OpenF1 /sessions endpoint to find the matching session.

    Args:
        year: Season year (e.g., 2025).
        country_name: Country name matching OpenF1's format (e.g., "Spain").
        session_name: Session type, default "Race".

    Returns:
        Integer session_key, or None if no matching session found.
    """
    data = await fetch_openf1("/sessions", {
        "year": year,
        "country_name": country_name,
        "session_name": session_name,
    })

    if not data:
        return None

    # OpenF1 returns a list — take the first (and usually only) match
    return data[0].get("session_key")
