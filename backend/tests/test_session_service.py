"""
Integration test for SessionService.

Pulls FP1, FP2, FP3, and Qualifying data for the 2025 Abu Dhabi Grand Prix
(the most recent completed GP) and verifies the data service works end-to-end.

First run downloads data from the F1 API (~2-4 min).
Subsequent runs use the local cache and finish in seconds.
"""

import pytest

from f1_strat.session_service import SessionService


@pytest.fixture(scope="module")
def weekend_data():
    """Load the full Abu Dhabi 2025 weekend once for all tests."""
    service = SessionService()
    return service.load_weekend(2025, "Abu Dhabi")


def test_all_sessions_loaded(weekend_data):
    """All four sessions (FP1, FP2, FP3, Q) should be present."""
    expected = {"Practice 1", "Practice 2", "Practice 3", "Qualifying"}
    assert expected == set(weekend_data.keys()), (
        f"Expected sessions {expected}, got {set(weekend_data.keys())}"
    )


def test_each_session_has_laps(weekend_data):
    """Every session should contain lap data."""
    for name, session in weekend_data.items():
        assert len(session["laps"]) > 0, f"{name} has no laps"


def test_each_session_has_weather(weekend_data):
    """Every session should contain weather samples."""
    for name, session in weekend_data.items():
        assert len(session["weather"]) > 0, f"{name} has no weather data"


def test_each_session_has_drivers(weekend_data):
    """Every session should list drivers."""
    for name, session in weekend_data.items():
        assert len(session["drivers"]) >= 18, (
            f"{name} has only {len(session['drivers'])} drivers"
        )


def test_laps_have_required_fields(weekend_data):
    """Spot-check that lap dicts contain the expected keys."""
    required = {"driver", "lap_number", "lap_time_s", "compound", "tyre_life", "stint"}
    for name, session in weekend_data.items():
        first_timed = next(
            (lap for lap in session["laps"] if lap["lap_time_s"] is not None),
            None,
        )
        if first_timed:
            missing = required - set(first_timed.keys())
            assert not missing, f"{name}: lap missing keys {missing}"


def test_qualifying_has_q_times(weekend_data):
    """The qualifying session should have Q1/Q2/Q3 times for top drivers."""
    qual = weekend_data.get("Qualifying")
    assert qual is not None, "Qualifying session not found"

    # Pole sitter should have q3 time
    pole = next(
        (r for r in qual["results"] if r["position"] == 1),
        None,
    )
    assert pole is not None, "No pole position result found"
    assert "q3_s" in pole, f"Pole sitter {pole['driver']} has no Q3 time"


def test_weather_values_sensible(weekend_data):
    """Weather values should be within physically reasonable ranges."""
    for name, session in weekend_data.items():
        for w in session["weather"]:
            if w["air_temp_c"] is not None:
                assert 0 < w["air_temp_c"] < 60, (
                    f"{name}: air temp {w['air_temp_c']}C out of range"
                )
            if w["track_temp_c"] is not None:
                assert 0 < w["track_temp_c"] < 80, (
                    f"{name}: track temp {w['track_temp_c']}C out of range"
                )


@pytest.fixture(scope="module")
def weather_summary():
    """Get weather summary for Abu Dhabi 2025 once for all weather tests."""
    service = SessionService()
    return service.get_weather_summary(2025, "Abu Dhabi")


def test_weather_summary_structure(weather_summary):
    """Weather summary should have the expected top-level keys."""
    assert "had_rain" in weather_summary
    assert "sessions" in weather_summary
    assert "overall" in weather_summary

    # Should have at least FP1 and FP2 (FP3 may be missing on sprint weekends)
    assert len(weather_summary["sessions"]) >= 2

    # Each session should have the summary fields
    for name, session in weather_summary["sessions"].items():
        assert "had_rain" in session
        assert "air_temp_range_c" in session
        assert "track_temp_range_c" in session
        assert "humidity_range_pct" in session
        assert "avg_wind_speed_ms" in session
        assert "sample_count" in session


def test_weather_summary_rain_flag(weather_summary):
    """The had_rain flag should be a boolean."""
    assert isinstance(weather_summary["had_rain"], bool)
    for session in weather_summary["sessions"].values():
        assert isinstance(session["had_rain"], bool)


def test_weather_summary_sensible_temps(weather_summary):
    """Air and track temps in the summary should be physically reasonable."""
    overall = weather_summary["overall"]

    if overall["air_temp_range_c"]["min"] is not None:
        assert 0 < overall["air_temp_range_c"]["min"] < 60
        assert 0 < overall["air_temp_range_c"]["max"] < 60
    if overall["track_temp_range_c"]["min"] is not None:
        assert 0 < overall["track_temp_range_c"]["min"] < 80
        assert 0 < overall["track_temp_range_c"]["max"] < 80


def test_print_summary(weekend_data, capsys):
    """Print a human-readable summary of the loaded data."""
    print("\n" + "=" * 70)
    print("  2025 Abu Dhabi Grand Prix — Weekend Data Summary")
    print("=" * 70)

    for name, session in weekend_data.items():
        laps = session["laps"]
        weather = session["weather"]
        drivers = session["drivers"]

        # Tyre compounds used
        compounds = sorted({
            lap["compound"]
            for lap in laps
            if lap["compound"] and lap["compound"] != "UNKNOWN"
        })

        # Weather range
        air_temps = [w["air_temp_c"] for w in weather if w["air_temp_c"] is not None]
        track_temps = [w["track_temp_c"] for w in weather if w["track_temp_c"] is not None]

        print(f"\n--- {name} ({session['date']}) ---")
        print(f"  Laps recorded:  {len(laps)}")
        print(f"  Drivers:        {len(drivers)}")
        print(f"  Tyre compounds: {', '.join(compounds)}")
        if air_temps:
            print(f"  Air temp:       {min(air_temps):.1f} - {max(air_temps):.1f} C")
        if track_temps:
            print(f"  Track temp:     {min(track_temps):.1f} - {max(track_temps):.1f} C")

        # Show fastest lap per session
        timed_laps = [lap for lap in laps if lap["lap_time_s"] is not None]
        if timed_laps:
            fastest = min(timed_laps, key=lambda x: x["lap_time_s"])
            mins, secs = divmod(fastest["lap_time_s"], 60)
            print(f"  Fastest lap:    {fastest['driver']} — {int(mins)}:{secs:06.3f}"
                  f" ({fastest['compound']})")

    print("\n" + "=" * 70)
    print("  All sessions loaded successfully!")
    print("=" * 70 + "\n")
