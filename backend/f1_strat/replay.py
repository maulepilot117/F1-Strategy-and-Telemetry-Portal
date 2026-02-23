"""
Replay engine — plays back historical race data through the live dashboard.

Pre-fetches all low-frequency OpenF1 data (positions, pits, race control,
intervals, stints, drivers) for a completed session, then replays it through
the same _update_from_* functions and SSE infrastructure that live_race uses.

High-frequency data (car_data, location) is too large to pre-fetch (~136MB),
so it's fetched in time-windowed chunks during playback.

Design:
- Reuses live_race._race_state, _state_changed, _update_from_* functions
- Only one of live/replay can be active (shared singleton state)
- Speed control is backend-side — the replay loop adjusts its virtual clock
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from f1_strat import live_race

logger = logging.getLogger(__name__)

# The background replay task — None when not replaying
_replay_task: asyncio.Task | None = None

# Pre-fetched timeline data — populated by start_replay()
_rc_timeline: list[dict] = []
_pit_timeline: list[dict] = []
_pos_timeline: list[dict] = []
_interval_timeline: list[dict] = []
_stints_data: list[dict] = []

# Index pointers — track how far through each timeline we've replayed.
# Each pointer marks the next record to process, so we never re-apply data.
_rc_idx: int = 0
_pit_idx: int = 0
_pos_idx: int = 0
_interval_idx: int = 0

# Session time boundaries (ISO strings) for progress calculation
_session_start: str | None = None
_session_end: str | None = None

# Flag to stop the replay loop cleanly
_stop_requested: bool = False


def _parse_dt(iso: str | None) -> datetime | None:
    """Parse an ISO timestamp string into a timezone-aware datetime.

    OpenF1 timestamps look like '2024-06-23T13:00:00.000000+00:00'.
    We handle both Z suffix and +00:00 offset formats.
    """
    if not iso:
        return None
    try:
        # Replace Z with +00:00 for fromisoformat compatibility
        cleaned = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _get_timestamps(data: list[dict]) -> list[datetime]:
    """Extract and parse all 'date' values from a list of OpenF1 records."""
    return [dt for d in data if (dt := _parse_dt(d.get("date"))) is not None]


async def start_replay(
    session_key: int,
    total_laps: int,
    year: int,
    grand_prix: str,
    speed: int = 4,
) -> None:
    """Start replaying a historical race session.

    1. Stops any active live tracking or previous replay
    2. Pre-fetches all low-frequency data in parallel
    3. Launches the replay loop as a background task

    Args:
        session_key: OpenF1 session identifier for the completed race.
        total_laps: Total laps in the race (for progress tracking).
        year: Season year (for strategy recalculation).
        grand_prix: GP name (for strategy recalculation).
        speed: Playback speed multiplier (1, 2, 4, or 8).
    """
    global _replay_task, _stop_requested
    global _rc_timeline, _pit_timeline, _pos_timeline, _interval_timeline, _stints_data
    global _rc_idx, _pit_idx, _pos_idx, _interval_idx
    global _session_start, _session_end

    # Stop any active live tracking or previous replay
    await live_race.stop_polling()
    await stop_replay()

    # Reset state for the new replay session
    live_race._race_state.update(live_race._empty_state())
    live_race._race_state["session_key"] = session_key
    live_race._race_state["total_laps"] = total_laps
    live_race._race_state["year"] = year
    live_race._race_state["grand_prix"] = grand_prix
    live_race._race_state["connected_to_openf1"] = True
    live_race._race_state["polling_active"] = True
    live_race._race_state["replay_mode"] = True
    live_race._race_state["replay_speed"] = speed
    live_race._race_state["replay_elapsed_pct"] = 0

    logger.info(
        "Starting replay: session %d, %d laps, speed %dx",
        session_key, total_laps, speed,
    )

    # Pre-fetch all low-frequency data in parallel — these are small enough
    # to load in one shot (~28K records total across all endpoints).
    params: dict[str, Any] = {"session_key": session_key}

    rc_task = live_race.fetch_openf1("/race_control", params)
    pit_task = live_race.fetch_openf1("/pit", params)
    pos_task = live_race.fetch_openf1("/position", params)
    interval_task = live_race.fetch_openf1("/intervals", params)
    stints_task = live_race.fetch_openf1("/stints", params)
    drivers_task = live_race.fetch_openf1("/drivers", params)

    results = await asyncio.gather(
        rc_task, pit_task, pos_task, interval_task, stints_task, drivers_task,
        return_exceptions=True,
    )

    # Unpack results — log errors but continue with whatever data we got
    for i, name in enumerate(["race_control", "pit", "position", "intervals", "stints", "drivers"]):
        if isinstance(results[i], Exception):
            logger.warning("Failed to fetch %s for replay: %s", name, results[i])

    _rc_timeline = results[0] if not isinstance(results[0], Exception) else []
    _pit_timeline = results[1] if not isinstance(results[1], Exception) else []
    _pos_timeline = results[2] if not isinstance(results[2], Exception) else []
    _interval_timeline = results[3] if not isinstance(results[3], Exception) else []
    _stints_data = results[4] if not isinstance(results[4], Exception) else []
    drivers_data = results[5] if not isinstance(results[5], Exception) else []

    # Populate drivers in race state
    for d in drivers_data:
        driver_num = d.get("driver_number")
        if driver_num is not None:
            live_race._race_state["drivers"][driver_num] = {
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

    # Tell frontend whether telemetry is available (replay supports it
    # if sponsor credentials are set, since we'll fetch car_data/location)
    live_race._race_state["telemetry_available"] = live_race._TELEMETRY_ENABLED

    # Determine session time boundaries from all timestamped data.
    # This defines the replay window: start to end.
    all_timestamps: list[datetime] = []
    for timeline in [_rc_timeline, _pit_timeline, _pos_timeline, _interval_timeline]:
        all_timestamps.extend(_get_timestamps(timeline))

    if not all_timestamps:
        logger.error("No timestamped data found for session %d — cannot replay", session_key)
        live_race._race_state["polling_active"] = False
        return

    _session_start = min(all_timestamps).isoformat()
    _session_end = max(all_timestamps).isoformat()

    logger.info(
        "Replay data loaded: %d rc, %d pit, %d pos, %d interval, %d stints, %d drivers",
        len(_rc_timeline), len(_pit_timeline), len(_pos_timeline),
        len(_interval_timeline), len(_stints_data), len(drivers_data),
    )

    # Reset timeline pointers
    _rc_idx = 0
    _pit_idx = 0
    _pos_idx = 0
    _interval_idx = 0
    _stop_requested = False

    # Launch the replay loop
    _replay_task = asyncio.create_task(_replay_loop(session_key, total_laps))
    _replay_task.add_done_callback(live_race._log_task_exception)


def _collect_records_up_to(
    timeline: list[dict],
    idx: int,
    virtual_clock: datetime,
) -> tuple[list[dict], int]:
    """Collect all records from timeline[idx:] with date <= virtual_clock.

    Returns the collected records and the new index pointer.
    This is efficient because timelines are sorted chronologically —
    we scan forward from the last pointer without revisiting old records.
    """
    collected = []
    while idx < len(timeline):
        record_dt = _parse_dt(timeline[idx].get("date"))
        if record_dt is None or record_dt <= virtual_clock:
            collected.append(timeline[idx])
            idx += 1
        else:
            break
    return collected, idx


def _apply_stints_for_lap(current_lap: int) -> None:
    """Apply stint data based on current lap number.

    Stints don't have timestamps — they're keyed by lap_start/lap_end.
    We find the active stint for each driver based on current_lap and
    feed it through the same _update_from_stints() function.

    For replay, we need to find the latest stint per driver where
    lap_start <= current_lap.
    """
    if not _stints_data:
        return

    # Find the most recent stint per driver for the current lap
    latest_per_driver: dict[int, dict] = {}
    for stint in _stints_data:
        driver_num = stint.get("driver_number")
        lap_start = stint.get("lap_start", 0)
        if driver_num is not None and lap_start <= current_lap:
            # Keep the one with the highest lap_start (most recent)
            existing = latest_per_driver.get(driver_num)
            if existing is None or lap_start > existing.get("lap_start", 0):
                latest_per_driver[driver_num] = stint

    if latest_per_driver:
        live_race._update_from_stints(list(latest_per_driver.values()))


async def _replay_loop(session_key: int, total_laps: int) -> None:
    """The replay playback loop — advances a virtual clock through the session.

    Each cycle:
    1. Advance virtual_clock by cycle_interval * replay_speed
    2. Collect all pre-fetched records up to virtual_clock
    3. Feed them through the _update_from_* functions
    4. Apply stint data based on current lap
    5. Optionally fetch car_data/location for telemetry
    6. Update state and notify SSE clients
    7. Sleep for cycle_interval real seconds

    Stops when virtual_clock passes session_end or stop is requested.
    """
    global _rc_idx, _pit_idx, _pos_idx, _interval_idx, _stop_requested

    if not _session_start or not _session_end:
        return

    start_dt = _parse_dt(_session_start)
    end_dt = _parse_dt(_session_end)
    if not start_dt or not end_dt:
        return

    virtual_clock = start_dt
    total_duration = (end_dt - start_dt).total_seconds()
    cycle_interval = 4.0  # Real seconds between replay ticks

    # Track last car_data/location fetch time for windowed requests
    last_telemetry_dt: str | None = None

    logger.info("Replay loop started: %s → %s", _session_start, _session_end)

    while not _stop_requested and live_race._race_state.get("polling_active"):
        speed = live_race._race_state.get("replay_speed", 1)

        # Speed 0 means paused — just sleep and check again
        if speed == 0:
            await asyncio.sleep(0.5)
            continue

        # Advance the virtual clock
        advance_seconds = cycle_interval * speed
        virtual_clock = virtual_clock.replace(
            tzinfo=timezone.utc,
        ) if virtual_clock.tzinfo is None else virtual_clock
        virtual_clock = datetime.fromtimestamp(
            virtual_clock.timestamp() + advance_seconds,
            tz=timezone.utc,
        )

        # Check if we've passed the session end
        if virtual_clock >= end_dt:
            virtual_clock = end_dt
            live_race._race_state["replay_elapsed_pct"] = 100

        # Collect and apply records up to virtual_clock
        pit_count_before = len(live_race._race_state["pit_log"])

        rc_records, _rc_idx = _collect_records_up_to(_rc_timeline, _rc_idx, virtual_clock)
        pit_records, _pit_idx = _collect_records_up_to(_pit_timeline, _pit_idx, virtual_clock)
        pos_records, _pos_idx = _collect_records_up_to(_pos_timeline, _pos_idx, virtual_clock)
        iv_records, _interval_idx = _collect_records_up_to(_interval_timeline, _interval_idx, virtual_clock)

        if rc_records:
            live_race._update_from_race_control(rc_records)
        if pit_records:
            live_race._update_from_pits(pit_records)
        if pos_records:
            live_race._update_from_positions(pos_records)
        if iv_records:
            live_race._update_from_intervals(iv_records)

        # Derive current lap from position data (positions have lap numbers
        # via the stints data, so we use the stint-based approach)
        _apply_stints_for_lap(live_race._race_state["current_lap"])

        # Also derive current lap from stints — find the max lap_end
        max_lap = 0
        for s in _stints_data:
            lap_end = s.get("lap_end")
            lap_start = s.get("lap_start", 0)
            if lap_end is not None and lap_end <= total_laps:
                # Only count stints that have ended before our virtual position
                # Use position data to estimate which lap we're on
                pass
            if lap_start is not None:
                # Check if this stint started before our virtual clock position
                # by cross-referencing with position timeline timestamps
                pass

        # Better approach: derive current lap from the latest position records
        # that have been applied — position records include date but not lap,
        # but stints have lap_start/lap_end without date.  Use the stints data
        # to determine current lap from the overall replay progress.
        if total_duration > 0:
            elapsed = (virtual_clock - start_dt).total_seconds()
            progress_pct = min(100, (elapsed / total_duration) * 100)
            live_race._race_state["replay_elapsed_pct"] = round(progress_pct, 1)

            # Estimate current lap from progress percentage
            estimated_lap = max(1, int((progress_pct / 100) * total_laps))
            if estimated_lap > live_race._race_state["current_lap"]:
                live_race._race_state["current_lap"] = estimated_lap
                # Re-apply stints for the new lap
                _apply_stints_for_lap(estimated_lap)

        # Fetch telemetry (car_data + location) in time-windowed chunks.
        # These are too large to pre-fetch, so we stream them during playback.
        if live_race._TELEMETRY_ENABLED:
            virtual_iso = virtual_clock.isoformat()
            try:
                telemetry_params: dict[str, Any] = {"session_key": session_key}
                if last_telemetry_dt:
                    telemetry_params["date>"] = last_telemetry_dt
                telemetry_params["date<="] = virtual_iso

                car_data, location = await asyncio.gather(
                    live_race.fetch_openf1("/car_data", telemetry_params),
                    live_race.fetch_openf1("/location", telemetry_params),
                    return_exceptions=True,
                )

                if not isinstance(car_data, Exception) and car_data:
                    live_race._update_from_car_data(car_data)
                if not isinstance(location, Exception) and location:
                    live_race._update_from_location(location)

                last_telemetry_dt = virtual_iso
            except Exception as e:
                logger.warning("Telemetry fetch failed during replay: %s", e)

        # Trigger strategy recalculation on pit events
        new_pits = len(live_race._race_state["pit_log"]) > pit_count_before
        no_strategies = not live_race._race_state.get("strategies")
        if (new_pits or no_strategies) and live_race._race_state["current_lap"] > 0:
            task = asyncio.create_task(live_race._maybe_recalculate())
            task.add_done_callback(live_race._log_task_exception)

        # Update timestamp and wake SSE clients
        live_race._race_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        async with live_race._state_changed:
            live_race._state_changed.notify_all()

        # Check if replay is complete
        if virtual_clock >= end_dt:
            logger.info("Replay complete for session %d", session_key)
            live_race._race_state["polling_active"] = False
            break

        # Sleep for the cycle interval (real time)
        await asyncio.sleep(cycle_interval)

    # Final state update
    live_race._race_state["connected_to_openf1"] = False
    async with live_race._state_changed:
        live_race._state_changed.notify_all()
    logger.info("Replay loop ended for session %d", session_key)


def set_replay_speed(speed: int) -> None:
    """Update the playback speed.  The loop picks it up on the next cycle.

    Args:
        speed: 0 (paused), 1, 2, 4, or 8.
    """
    live_race._race_state["replay_speed"] = speed
    logger.info("Replay speed set to %dx", speed)


async def stop_replay() -> None:
    """Stop the replay loop and reset state."""
    global _replay_task, _stop_requested

    _stop_requested = True
    live_race._race_state["polling_active"] = False

    if _replay_task is not None and not _replay_task.done():
        _replay_task.cancel()
        try:
            await _replay_task
        except asyncio.CancelledError:
            pass
        logger.info("Replay task cancelled")

    _replay_task = None

    # Reset replay-specific state fields
    live_race._race_state["replay_mode"] = False
    live_race._race_state["replay_speed"] = 1
    live_race._race_state["replay_elapsed_pct"] = 0
