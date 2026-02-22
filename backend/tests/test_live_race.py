"""Tests for the live race tracking module.

Uses httpx.MockTransport with recorded JSON fixtures so we never hit
the real OpenF1 API during tests.  Tests the individual state update
functions and the fetch_openf1() error handling — NOT the polling loop
itself (too complex to unit test async scheduling; verified manually).
"""

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from f1_strat import live_race

# ---------------------------------------------------------------------------
# Fixtures directory
# ---------------------------------------------------------------------------
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "openf1"


def _load_fixture(name: str) -> list[dict]:
    """Load a JSON fixture file and return the parsed list."""
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Mock transport for httpx — returns fixture data based on URL path
# ---------------------------------------------------------------------------

def _make_mock_transport() -> httpx.MockTransport:
    """Create a mock transport that serves fixture files based on endpoint path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        # Map endpoint paths to fixture files
        fixture_map = {
            "/v1/sessions": "sessions.json",
            "/v1/drivers": "drivers.json",
            "/v1/race_control": "race_control.json",
            "/v1/pit": "pit.json",
            "/v1/stints": "stints.json",
            "/v1/position": "position.json",
            "/v1/intervals": "intervals.json",
        }

        fixture_name = fixture_map.get(path)
        if fixture_name:
            data = (FIXTURES_DIR / fixture_name).read_text()
            return httpx.Response(200, json=json.loads(data))

        return httpx.Response(404, json=[])

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the live_race module state before each test."""
    live_race._race_state.update(live_race._empty_state())
    live_race._backoff_seconds = 0.0
    live_race._sse_client_count = 0
    for key in live_race._last_timestamps:
        live_race._last_timestamps[key] = None
    # Force-close any existing client (sync — ok for test cleanup)
    live_race._http_client = None
    yield


# ---------------------------------------------------------------------------
# Tests: fetch_openf1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_openf1_success():
    """fetch_openf1 returns parsed JSON data from OpenF1."""
    # Replace the client with our mock
    live_race._http_client = httpx.AsyncClient(
        transport=_make_mock_transport(),
        base_url="https://api.openf1.org/v1",
    )

    data = await live_race.fetch_openf1("/sessions", {"year": 2024})
    assert len(data) == 1
    assert data[0]["session_key"] == 9539
    assert data[0]["country_name"] == "Spain"

    await live_race.close_client()


@pytest.mark.asyncio
async def test_fetch_openf1_rate_limit():
    """fetch_openf1 handles 429 by setting backoff and returning empty list."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"}, json=[])

    live_race._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openf1.org/v1",
    )

    result = await live_race.fetch_openf1("/race_control")
    assert result == []
    assert live_race._backoff_seconds >= 30

    await live_race.close_client()


@pytest.mark.asyncio
async def test_fetch_openf1_server_error():
    """fetch_openf1 handles 500 gracefully — returns empty list."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    live_race._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openf1.org/v1",
    )

    result = await live_race.fetch_openf1("/race_control")
    assert result == []

    await live_race.close_client()


@pytest.mark.asyncio
async def test_fetch_openf1_timeout():
    """fetch_openf1 handles timeouts gracefully."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    live_race._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openf1.org/v1",
    )

    result = await live_race.fetch_openf1("/race_control")
    assert result == []

    await live_race.close_client()


# ---------------------------------------------------------------------------
# Tests: safety car detection
# ---------------------------------------------------------------------------

def test_safety_car_deployed():
    """SC message sets is_safety_car to True."""
    messages = [
        {"message": "SAFETY CAR DEPLOYED", "lap_number": 12, "category": "SafetyCar", "flag": None, "date": "2024-06-23T13:20:00+00:00"},
    ]
    live_race._update_from_race_control(messages)

    assert live_race._race_state["is_safety_car"] is True
    assert len(live_race._race_state["race_control_log"]) == 1
    assert live_race._race_state["last_race_control_message"] == "SAFETY CAR DEPLOYED"


def test_vsc_deployed():
    """VSC message sets is_safety_car to True."""
    messages = [
        {"message": "VIRTUAL SAFETY CAR DEPLOYED", "lap_number": 30, "category": "SafetyCar", "flag": None, "date": "2024-06-23T13:40:00+00:00"},
    ]
    live_race._update_from_race_control(messages)
    assert live_race._race_state["is_safety_car"] is True


def test_safety_car_ending():
    """'SAFETY CAR IN THIS LAP' clears the SC flag."""
    live_race._race_state["is_safety_car"] = True
    messages = [
        {"message": "SAFETY CAR IN THIS LAP", "lap_number": 15, "category": "SafetyCar", "flag": None, "date": "2024-06-23T13:25:00+00:00"},
    ]
    live_race._update_from_race_control(messages)
    assert live_race._race_state["is_safety_car"] is False


def test_vsc_ending():
    """'VSC ENDING' clears the SC flag."""
    live_race._race_state["is_safety_car"] = True
    messages = [
        {"message": "VSC ENDING", "lap_number": 32, "category": "SafetyCar", "flag": None, "date": "2024-06-23T13:43:00+00:00"},
    ]
    live_race._update_from_race_control(messages)
    assert live_race._race_state["is_safety_car"] is False


def test_green_flag_clears_sc():
    """GREEN flag after SC period clears the flag."""
    live_race._race_state["is_safety_car"] = True
    messages = [
        {"message": "GREEN FLAG", "lap_number": 16, "category": "Flag", "flag": "GREEN", "date": "2024-06-23T13:26:00+00:00"},
    ]
    live_race._update_from_race_control(messages)
    assert live_race._race_state["is_safety_car"] is False


def test_race_control_log_truncated():
    """Race control log is capped at 50 messages."""
    messages = [
        {"message": f"Message {i}", "lap_number": i, "category": "Other", "flag": None, "date": f"2024-06-23T13:{i:02d}:00+00:00"}
        for i in range(60)
    ]
    live_race._update_from_race_control(messages)
    assert len(live_race._race_state["race_control_log"]) == 50


# ---------------------------------------------------------------------------
# Tests: pit stop updates
# ---------------------------------------------------------------------------

def test_pit_stops_recorded():
    """Pit events are added to pit_log."""
    pit_events = _load_fixture("pit.json")
    live_race._update_from_pits(pit_events)

    assert len(live_race._race_state["pit_log"]) == 3
    assert live_race._race_state["pit_log"][0]["driver_number"] == 1
    assert live_race._race_state["pit_log"][0]["lap"] == 17
    assert live_race._race_state["pit_log"][0]["duration_s"] == 21.5


def test_pit_stops_no_duplicates():
    """Duplicate pit events are ignored."""
    pit_events = _load_fixture("pit.json")
    live_race._update_from_pits(pit_events)
    live_race._update_from_pits(pit_events)  # second call with same data

    assert len(live_race._race_state["pit_log"]) == 3  # still 3, not 6


# ---------------------------------------------------------------------------
# Tests: stint/compound updates
# ---------------------------------------------------------------------------

def test_stint_updates_compound():
    """Stint data updates driver compound and tyre age."""
    # Set up a driver first
    live_race._race_state["drivers"][1] = {
        "driver_number": 1, "current_compound": "UNKNOWN", "tyre_age": 0,
        "compounds_used": [], "stops_completed": 0,
    }
    live_race._race_state["current_lap"] = 30

    stints = [
        {"driver_number": 1, "compound": "MEDIUM", "tyre_age_at_start": 0,
         "lap_start": 18, "lap_end": 44, "stint_number": 2},
    ]
    live_race._update_from_stints(stints)

    driver = live_race._race_state["drivers"][1]
    assert driver["current_compound"] == "MEDIUM"
    assert driver["tyre_age"] == 12  # lap 30 - lap 18 + 0
    assert "MEDIUM" in driver["compounds_used"]
    assert driver["stops_completed"] == 1  # stint 2 means 1 stop


# ---------------------------------------------------------------------------
# Tests: position updates
# ---------------------------------------------------------------------------

def test_position_updates():
    """Position data updates driver positions."""
    live_race._race_state["drivers"][1] = {"position": 0}
    live_race._race_state["drivers"][4] = {"position": 0}

    positions = _load_fixture("position.json")
    live_race._update_from_positions(positions)

    # Should use the LATEST position for each driver
    assert live_race._race_state["drivers"][1]["position"] == 2  # changed from 1 to 2
    assert live_race._race_state["drivers"][4]["position"] == 1  # changed from 2 to 1


# ---------------------------------------------------------------------------
# Tests: interval updates
# ---------------------------------------------------------------------------

def test_interval_updates():
    """Interval data updates gap_to_leader and interval."""
    live_race._race_state["drivers"][1] = {"gap_to_leader": 0, "interval": 0}
    live_race._race_state["drivers"][4] = {"gap_to_leader": 0, "interval": 0}

    intervals = _load_fixture("intervals.json")
    live_race._update_from_intervals(intervals)

    assert live_race._race_state["drivers"][1]["gap_to_leader"] == 0.0
    assert live_race._race_state["drivers"][4]["gap_to_leader"] == 3.456
    assert live_race._race_state["drivers"][4]["interval"] == 3.456


# ---------------------------------------------------------------------------
# Tests: session key resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_session_key():
    """resolve_session_key returns session_key from OpenF1."""
    live_race._http_client = httpx.AsyncClient(
        transport=_make_mock_transport(),
        base_url="https://api.openf1.org/v1",
    )

    key = await live_race.resolve_session_key(2024, "Spain", "Race")
    assert key == 9539

    await live_race.close_client()


@pytest.mark.asyncio
async def test_resolve_session_key_not_found():
    """resolve_session_key returns None when no session matches."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    live_race._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openf1.org/v1",
    )

    key = await live_race.resolve_session_key(2030, "Mars", "Race")
    assert key is None

    await live_race.close_client()


# ---------------------------------------------------------------------------
# Tests: empty state helper
# ---------------------------------------------------------------------------

def test_empty_state():
    """_empty_state returns a fresh state dict with all expected keys."""
    state = live_race._empty_state()
    assert state["session_key"] is None
    assert state["current_lap"] == 0
    assert state["is_safety_car"] is False
    assert state["drivers"] == {}
    assert state["race_control_log"] == []
    assert state["pit_log"] == []
    assert state["polling_active"] is False
