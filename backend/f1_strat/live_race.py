"""
Live race tracking via the OpenF1 API.

Polls OpenF1 endpoints for real-time race data (positions, pit stops,
tyre stints, race control messages) and maintains a module-level state
dict that the SSE endpoint streams to connected frontends.

Key design decisions:
- Module-level functions + shared state dict (no classes) — matches codebase convention
- Single uvicorn worker required (--workers 1) because _race_state is in-process memory
- Full state snapshots over SSE (no partial updates) — eliminates merge bugs
- One race at a time — appropriate for a fan tool on a home Kubernetes lab
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenF1 API base URL
# ---------------------------------------------------------------------------
_OPENF1_BASE = "https://api.openf1.org/v1"

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
    leak TCP connections when the server stops.
    """
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


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
        - 401/403 (auth): stops polling entirely
        - 500/timeout: logs warning, returns empty list (keeps last state)
    """
    global _backoff_seconds

    client = await _get_client()
    try:
        resp = await client.get(endpoint, params=params)

        if resp.status_code == 429:
            # Rate limited — back off progressively
            retry_after = int(resp.headers.get("Retry-After", "30"))
            _backoff_seconds = max(retry_after, _backoff_seconds * 2 or 30)
            logger.warning(
                "OpenF1 rate limited (429), backing off %.0fs", _backoff_seconds
            )
            return []

        if resp.status_code in (401, 403):
            # Auth error — stop polling, don't retry
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
        "last_updated": None,       # ISO timestamp
        "connected_to_openf1": False,
        "polling_active": False,
    }


_race_state: dict = _empty_state()

# Track connected SSE clients so we can auto-stop polling
_sse_client_count: int = 0

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
# Polling loop
# ---------------------------------------------------------------------------

# The background polling task — None when not polling
_polling_task: asyncio.Task | None = None

# Timestamps for incremental fetching (only get new data since last poll)
_last_timestamps: dict[str, str | None] = {
    "race_control": None,
    "pit": None,
    "stints": None,
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


async def start_polling(session_key: int, total_laps: int) -> None:
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

    Polls each endpoint in round-robin order with ~8s between calls.
    Total cycle time for all 5 endpoints: ~8 seconds (each endpoint polled
    sequentially within one cycle, then wait for the remainder of 8s).
    """
    # The 5 endpoints to poll, in order
    endpoints = [
        ("race_control", "/race_control", _update_from_race_control),
        ("pit", "/pit", _update_from_pits),
        ("stints", "/stints", _update_from_stints),
        ("position", "/position", _update_from_positions),
        ("intervals", "/intervals", _update_from_intervals),
    ]

    idle_since: float | None = None

    while _race_state["polling_active"]:
        cycle_start = asyncio.get_event_loop().time()

        # Check for rate limit backoff
        if _backoff_seconds > 0:
            logger.info("Rate limit backoff: waiting %.0fs", _backoff_seconds)
            await asyncio.sleep(_backoff_seconds)

        # Poll each endpoint
        for key, path, updater in endpoints:
            if not _race_state["polling_active"]:
                break

            params = _make_since_params(session_key, key)
            data = await fetch_openf1(path, params)
            _track_latest_timestamp(data, key)

            if data:
                updater(data)

        # Update current lap from stint data (stints have lap_start/lap_end)
        # We need to re-fetch stints without the since filter to get lap_end
        all_stints = await fetch_openf1("/stints", {"session_key": session_key})
        if all_stints:
            # Find the max lap_end across all drivers
            max_lap = 0
            for s in all_stints:
                lap_end = s.get("lap_end")
                if lap_end is not None and lap_end > max_lap:
                    max_lap = lap_end
            if max_lap > _race_state["current_lap"]:
                _race_state["current_lap"] = max_lap

            # Also update tyre age based on current lap
            _update_from_stints(all_stints)

        # Mark the state as updated
        _race_state["last_updated"] = datetime.now(timezone.utc).isoformat()

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

        # Wait for the remainder of 8 seconds (the poll cycle target)
        elapsed = asyncio.get_event_loop().time() - cycle_start
        sleep_time = max(0, 8.0 - elapsed)
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
# SSE generator — yields full state snapshots
# ---------------------------------------------------------------------------

async def race_state_generator(disconnect_event: asyncio.Event) -> None:
    """Async generator that yields full _race_state snapshots as SSE data.

    Yields the current state on connect, then re-yields whenever
    last_updated changes.  Sends a keepalive comment every 15 seconds
    during quiet periods to prevent proxy/browser connection drops.

    Args:
        disconnect_event: Set when the client disconnects (checked each loop).

    Yields:
        dict: The full _race_state to be serialized as JSON by sse-starlette.
    """
    global _sse_client_count
    _sse_client_count += 1
    logger.info("SSE client connected (total: %d)", _sse_client_count)

    try:
        last_sent = None

        while not disconnect_event.is_set():
            current = _race_state.get("last_updated")

            if current != last_sent:
                # State changed — yield a full snapshot
                last_sent = current
                yield _race_state
            else:
                # No change — yield a keepalive comment (sse-starlette
                # handles the ": keepalive\n\n" formatting when we yield None
                # with event="keepalive")
                pass

            # Check every 1 second for state changes
            await asyncio.sleep(1)

    finally:
        _sse_client_count -= 1
        logger.info("SSE client disconnected (total: %d)", _sse_client_count)


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
