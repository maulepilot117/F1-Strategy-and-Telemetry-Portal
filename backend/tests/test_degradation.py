"""
Integration test for DegradationService.

Uses the 2024 Spanish Grand Prix at Circuit de Barcelona-Catalunya — a good
test case because it's a traditional circuit where teams run all three dry
compounds extensively in practice, giving us rich degradation data.

First run downloads from the F1 API.  Subsequent runs use the local cache.
"""

import pytest

from f1_strat.degradation import DegradationService


@pytest.fixture(scope="module")
def degradation_data():
    """Analyze the 2024 Spanish GP once for all tests in this module."""
    service = DegradationService()
    return service.analyze(2024, "Spain")


def test_all_dry_compounds_present(degradation_data):
    """All three dry compounds (SOFT, MEDIUM, HARD) should have data."""
    compounds = set(degradation_data["compounds"].keys())
    expected = {"SOFT", "MEDIUM", "HARD"}
    assert expected == compounds, (
        f"Expected {expected}, got {compounds}"
    )


def test_degradation_is_positive(degradation_data):
    """Every compound should show positive degradation (tyres get slower)."""
    for name, data in degradation_data["compounds"].items():
        assert data["degradation_per_lap_s"] > 0, (
            f"{name} shows negative degradation ({data['degradation_per_lap_s']}s/lap) "
            f"— tyres should always get slower, not faster"
        )


def test_soft_degrades_fastest(degradation_data):
    """SOFT should degrade faster than MEDIUM, which degrades faster than HARD.

    This is a fundamental property of F1 tyres — softer rubber grips more
    but wears out faster.  If our analysis doesn't show this ordering,
    something is wrong with the algorithm.
    """
    compounds = degradation_data["compounds"]
    soft_rate = compounds["SOFT"]["degradation_per_lap_s"]
    medium_rate = compounds["MEDIUM"]["degradation_per_lap_s"]
    hard_rate = compounds["HARD"]["degradation_per_lap_s"]

    assert soft_rate > medium_rate, (
        f"SOFT ({soft_rate:.4f}s/lap) should degrade faster than "
        f"MEDIUM ({medium_rate:.4f}s/lap)"
    )
    assert medium_rate > hard_rate, (
        f"MEDIUM ({medium_rate:.4f}s/lap) should degrade faster than "
        f"HARD ({hard_rate:.4f}s/lap)"
    )


def test_curves_have_multiple_points(degradation_data):
    """Each compound's curve should have several tyre age data points."""
    for name, data in degradation_data["compounds"].items():
        curve = data["curve"]
        assert len(curve) >= 3, (
            f"{name} curve has only {len(curve)} points — need at least 3 "
            f"to see a meaningful trend"
        )


def test_curve_starts_near_zero(degradation_data):
    """The first point on each curve (tyre age 1) should be near zero.

    Tyre age 1 is the first lap on fresh tyres — the baseline.  The delta
    should be 0.0 or very close to it.
    """
    for name, data in degradation_data["compounds"].items():
        first_point = data["curve"][0]
        assert first_point["tyre_age"] == 1, (
            f"{name} curve doesn't start at tyre age 1"
        )
        # Allow a small tolerance — the average across stints won't be
        # exactly 0 because not every stint starts from the same baseline
        assert abs(first_point["avg_delta_s"]) < 0.5, (
            f"{name} first point delta is {first_point['avg_delta_s']}s — "
            f"should be near 0"
        )


def test_fuel_correction_applied(degradation_data):
    """The fuel correction value should be recorded in the output."""
    assert degradation_data["fuel_correction_s_per_lap"] == 0.07


def test_event_metadata(degradation_data):
    """Basic metadata should be present and correct."""
    assert degradation_data["year"] == 2024
    assert "Spanish" in degradation_data["event_name"] or "Spain" in degradation_data["event_name"]


def test_sample_counts_reasonable(degradation_data):
    """Early tyre ages should have at least 2 samples.

    We require 5+ lap stints and filter aggressively for quality, so
    harder compounds (which teams run less in practice) may only have
    2-3 qualifying stints.  Softer compounds typically have more.
    """
    for name, data in degradation_data["compounds"].items():
        first_point = data["curve"][0]
        assert first_point["sample_count"] >= 2, (
            f"{name} has only {first_point['sample_count']} samples at "
            f"tyre age 1 — expected at least 2 from practice sessions"
        )


def test_weather_summary_in_degradation(degradation_data):
    """Degradation response should now include a weather summary."""
    assert "weather_summary" in degradation_data
    ws = degradation_data["weather_summary"]
    assert "had_rain" in ws
    assert "sessions" in ws
    assert "overall" in ws


def test_wet_compounds_flag(degradation_data):
    """Spain 2024 was dry — wet_compounds_available should be False."""
    assert "wet_compounds_available" in degradation_data
    # Spain 2024 practice was dry, so no one ran inters or wets
    assert degradation_data["wet_compounds_available"] is False


def test_race_info_in_response(degradation_data):
    """Degradation response should include race info auto-populated from race data."""
    assert "race_laps" in degradation_data, (
        "Expected 'race_laps' in degradation response — "
        "get_race_info() should provide the official race distance"
    )
    assert "avg_pit_stop_loss_s" in degradation_data, (
        "Expected 'avg_pit_stop_loss_s' in degradation response — "
        "get_race_info() should compute pit stop loss from real data"
    )


def test_race_laps_correct(degradation_data):
    """Spain 2024 should be 66 laps — the official race distance."""
    assert degradation_data["race_laps"] == 66, (
        f"Expected 66 race laps for Spain 2024, got {degradation_data['race_laps']}"
    )


def test_pit_stop_loss_reasonable(degradation_data):
    """Pit stop loss for Barcelona should be between 18-30s.

    Barcelona has a medium-length pit lane.  The exact value depends on
    the year's cars and pit crew performance, but it should be in this
    range for any modern F1 season.
    """
    loss = degradation_data["avg_pit_stop_loss_s"]
    assert 18.0 <= loss <= 30.0, (
        f"Pit stop loss of {loss}s is outside the expected 18-30s range "
        f"for Barcelona — check the calculation"
    )


def test_print_summary(degradation_data):
    """Print a human-readable summary of the degradation analysis."""
    print("\n" + "=" * 70)
    print(f"  {degradation_data['event_name']} {degradation_data['year']}"
          f" — Tyre Degradation Analysis")
    print(f"  Fuel correction: {degradation_data['fuel_correction_s_per_lap']}s/lap")
    if "race_laps" in degradation_data:
        print(f"  Race laps: {degradation_data['race_laps']}")
    if "avg_pit_stop_loss_s" in degradation_data:
        print(f"  Pit stop loss: {degradation_data['avg_pit_stop_loss_s']}s")
    print("=" * 70)

    # Sort compounds by degradation rate (highest first)
    sorted_compounds = sorted(
        degradation_data["compounds"].items(),
        key=lambda x: x[1]["degradation_per_lap_s"],
        reverse=True,
    )

    for name, data in sorted_compounds:
        rate = data["degradation_per_lap_s"]
        curve = data["curve"]
        max_age = curve[-1]["tyre_age"] if curve else 0

        print(f"\n  {name}")
        print(f"    Degradation rate: {rate:.4f} s/lap")
        print(f"    Data points:      {len(curve)} (up to {max_age} laps)")
        print(f"    Curve:")

        for point in curve[:12]:  # Show first 12 laps
            bar = "+" * int(point["avg_delta_s"] * 10)  # Simple ASCII chart
            print(
                f"      Lap {point['tyre_age']:2d}: "
                f"{point['avg_delta_s']:+.3f}s  "
                f"(n={point['sample_count']:2d})  {bar}"
            )
        if len(curve) > 12:
            print(f"      ... ({len(curve) - 12} more laps)")

    print("\n" + "=" * 70)
    print("  Analysis complete!")
    print("=" * 70 + "\n")


def test_compound_offsets_present(degradation_data):
    """Compound offsets should be present in the degradation response.

    These offsets capture the inherent pace gap between compounds on
    fresh tyres — e.g., SOFT is ~0.5-1.5s/lap faster than HARD when new.
    The fastest compound gets offset 0.0, slower ones get positive values.
    """
    assert "compound_offsets" in degradation_data, (
        "Expected 'compound_offsets' in degradation response"
    )
    offsets = degradation_data["compound_offsets"]

    # Should have at least 2 compounds with offsets
    assert len(offsets) >= 2, (
        f"Expected at least 2 compound offsets, got {offsets}"
    )

    # The fastest compound should have offset 0.0
    min_offset = min(offsets.values())
    assert min_offset == 0.0, (
        f"Fastest compound should have offset 0.0, got {min_offset}"
    )

    # All offsets should be non-negative (relative to the fastest)
    for compound, offset in offsets.items():
        assert offset >= 0.0, (
            f"{compound} has negative offset {offset}s — "
            f"offsets should be relative to the fastest compound"
        )

    # SOFT should generally be the fastest (offset 0 or near 0),
    # and HARD should be slowest (highest offset).  We check that
    # HARD's offset is > SOFT's offset — this is a fundamental
    # property of F1 tyres.
    if "SOFT" in offsets and "HARD" in offsets:
        assert offsets["HARD"] > offsets["SOFT"], (
            f"HARD offset ({offsets['HARD']}s) should be greater than "
            f"SOFT offset ({offsets['SOFT']}s) — harder tyres are slower"
        )

    # Print for visual inspection
    print(f"\n  Compound base pace offsets: {offsets}")


def test_compound_offsets_reasonable(degradation_data):
    """Compound offsets should be in a physically realistic range.

    In F1, the gap between SOFT and HARD on fresh tyres is typically
    0.5-2.0s/lap.  Anything outside this range suggests a data issue.
    """
    offsets = degradation_data["compound_offsets"]
    if "HARD" in offsets and "SOFT" in offsets:
        gap = offsets["HARD"] - offsets["SOFT"]
        assert 0.2 < gap < 3.0, (
            f"HARD-SOFT offset gap is {gap:.2f}s — expected 0.2-3.0s. "
            f"Full offsets: {offsets}"
        )


def test_deg_coefficients_present(degradation_data):
    """Each compound should include deg_coefficients with linear and quadratic keys.

    The strategy engine uses these coefficients for more accurate lap simulation.
    'linear' is the per-lap degradation rate (same as degree-1 slope, but may
    differ when enough data exists for a degree-2 fit).  'quadratic' captures
    curvature — negative means flattening degradation (typical for real tyres).
    When insufficient data exists, quadratic=0 (identical to pure linear model).
    """
    for compound, info in degradation_data["compounds"].items():
        assert "deg_coefficients" in info, (
            f"{compound} missing 'deg_coefficients' — "
            f"_build_curves() should always include this key"
        )
        coeffs = info["deg_coefficients"]
        assert "linear" in coeffs, f"{compound} deg_coefficients missing 'linear'"
        assert "quadratic" in coeffs, f"{compound} deg_coefficients missing 'quadratic'"
        # Linear should be positive (tyres always degrade)
        assert coeffs["linear"] > 0, (
            f"{compound} linear coefficient {coeffs['linear']} should be positive"
        )
        # Quadratic should be a number (can be positive, negative, or zero)
        assert isinstance(coeffs["quadratic"], (int, float)), (
            f"{compound} quadratic coefficient should be a number, "
            f"got {type(coeffs['quadratic'])}"
        )


def test_deg_rate_floor(degradation_data):
    """All compounds should have a minimum deg rate of 0.02 s/lap.

    Negative or near-zero rates are noise from track evolution in practice
    sessions.  The floor prevents the strategy engine from thinking tyres
    get faster with age (which would push pit stops to race end).
    """
    for compound, info in degradation_data["compounds"].items():
        rate = info["degradation_per_lap_s"]
        assert rate >= 0.02, (
            f"{compound} deg rate {rate:.4f} is below the 0.02 floor"
        )


def test_race_track_temp_in_response(degradation_data):
    """Degradation response should include the race-day track temperature.

    Spain 2024 was a warm race — track temp should be in a reasonable
    range (25-55°C).  This value is used to weight practice stints by
    temperature proximity to the race.
    """
    assert "race_track_temp_c" in degradation_data, (
        "Expected 'race_track_temp_c' in response — "
        "_get_race_track_temp() should return the race-day track temp"
    )
    temp = degradation_data["race_track_temp_c"]
    assert 25.0 <= temp <= 55.0, (
        f"Race track temp {temp}°C is outside expected 25-55°C range "
        f"for Barcelona in June"
    )


def test_temperature_weighting_unit():
    """Test the Gaussian temperature weight function directly.

    Verifies the math:
    - Same temp → weight 1.0
    - 10°C diff → weight ~0.61 (e^(-0.5))
    - 20°C diff → weight ~0.14 (e^(-2.0))
    - None inputs → weight 1.0 (fallback to equal weighting)
    """
    service = DegradationService()

    # Same temperature → perfect weight
    w = service._compute_temp_weight(30.0, 30.0)
    assert abs(w - 1.0) < 0.001, f"Same temp should give 1.0, got {w}"

    # 10°C difference → ~0.607 (e^(-0.5))
    w = service._compute_temp_weight(40.0, 30.0)
    assert 0.59 < w < 0.63, f"10°C diff should give ~0.61, got {w}"

    # 20°C difference → ~0.135 (e^(-2.0))
    w = service._compute_temp_weight(50.0, 30.0)
    assert 0.12 < w < 0.16, f"20°C diff should give ~0.14, got {w}"

    # Symmetric: sign of difference shouldn't matter
    w_pos = service._compute_temp_weight(40.0, 30.0)
    w_neg = service._compute_temp_weight(20.0, 30.0)
    assert abs(w_pos - w_neg) < 0.001, (
        f"Weight should be symmetric: +10°C={w_pos}, -10°C={w_neg}"
    )

    # None race temp → fallback to 1.0 (future race, no data)
    w = service._compute_temp_weight(40.0, None)
    assert w == 1.0, f"None race temp should give 1.0, got {w}"

    # None stint temp → fallback to 1.0 (no weather data for session)
    w = service._compute_temp_weight(None, 30.0)
    assert w == 1.0, f"None stint temp should give 1.0, got {w}"

    # NaN inputs → fallback to 1.0
    w = service._compute_temp_weight(float("nan"), 30.0)
    assert w == 1.0, f"NaN stint temp should give 1.0, got {w}"


# ---- Historical blending tests ----


@pytest.fixture(scope="module")
def degradation_with_history():
    """Analyze Spain 2024 with 3 years of historical race data.

    This is the same GP as degradation_data but explicitly includes
    historical races (2023, 2022, 2021) for quadratic stabilization.
    """
    service = DegradationService()
    return service.analyze(2024, "Spain", history_years=3)


@pytest.fixture(scope="module")
def degradation_no_history():
    """Analyze Spain 2024 with NO historical data (practice only).

    Used to verify that the linear coefficients are the same with and
    without history — only the quadratic coefficient should change.
    """
    service = DegradationService()
    return service.analyze(2024, "Spain", history_years=0)


def test_historical_years_used(degradation_with_history):
    """Spain 2024 should have historical years in the response.

    With history_years=3, we expect data from 2021, 2022, and/or 2023.
    Some years may be missing (e.g. circuit changes), but at least one
    should be present.
    """
    assert "historical_years_used" in degradation_with_history, (
        "Expected 'historical_years_used' in response — "
        "_load_historical_race_stints() should find at least one year"
    )
    years = degradation_with_history["historical_years_used"]
    assert len(years) >= 1, f"Expected at least 1 historical year, got {years}"
    # All years should be before 2024
    for y in years:
        assert y < 2024, f"Historical year {y} should be before 2024"
    assert "historical_data_points" in degradation_with_history
    assert degradation_with_history["historical_data_points"] > 0


def test_no_history_fallback(degradation_no_history):
    """history_years=0 should produce valid data without historical metadata.

    The response should still have compounds with degradation curves,
    but should NOT contain historical_years_used or historical_data_points.
    """
    assert "historical_years_used" not in degradation_no_history, (
        "history_years=0 should not include historical_years_used"
    )
    assert "historical_data_points" not in degradation_no_history, (
        "history_years=0 should not include historical_data_points"
    )
    # Should still have valid compound data
    assert len(degradation_no_history["compounds"]) > 0, (
        "history_years=0 should still produce compound data from practice"
    )


def test_linear_from_practice_only(degradation_with_history, degradation_no_history):
    """Linear coefficients should be identical with and without historical data.

    The linear coefficient (deg rate) comes from practice-only degree-1 fit.
    Historical data only affects the quadratic coefficient (curvature).
    """
    for compound in degradation_no_history["compounds"]:
        if compound not in degradation_with_history["compounds"]:
            continue
        no_hist = degradation_no_history["compounds"][compound]
        with_hist = degradation_with_history["compounds"][compound]

        # degradation_per_lap_s should be identical — same practice data
        assert no_hist["degradation_per_lap_s"] == with_hist["degradation_per_lap_s"], (
            f"{compound}: deg rate changed with history "
            f"({no_hist['degradation_per_lap_s']} vs {with_hist['degradation_per_lap_s']})"
        )


def test_quadratic_coefficient_with_history(degradation_with_history):
    """At least one compound should have a non-zero quadratic coefficient.

    With 3 years of historical data providing 100+ stints, the quadratic
    fit should have enough data to detect curvature for at least one compound.
    """
    has_nonzero_quad = False
    for compound, info in degradation_with_history["compounds"].items():
        coeffs = info["deg_coefficients"]
        if coeffs["quadratic"] != 0.0:
            has_nonzero_quad = True
            print(
                f"  {compound}: quad={coeffs['quadratic']:.6f}, "
                f"linear={coeffs['linear']:.4f}"
            )

    assert has_nonzero_quad, (
        "Expected at least one compound to have a non-zero quadratic "
        "coefficient with 3 years of historical data"
    )


# ---- Historical-only fallback tests ----


def test_build_curves_skips_historical_only():
    """When practice data is empty, _build_curves() should return empty dict.

    Historical-only compounds are intentionally skipped because historical
    race rates are poorly calibrated for strategy predictions — race data
    shows lower degradation than practice due to tyre management, track
    evolution, and rubber build-up.  The strategy engine's fallback rates
    (HARD=60%×MEDIUM, SOFT=160%×MEDIUM) are better calibrated.

    Historical data IS still used to stabilize the quadratic coefficient
    for compounds that have practice data (tested elsewhere).
    """
    import numpy as np
    import pandas as pd

    service = DegradationService()

    # Empty practice deltas — simulates no usable practice data
    empty_deltas = pd.DataFrame()

    # Synthetic historical data that would be available
    np.random.seed(42)
    records = []
    for stint_offset in range(3):
        for age in range(1, 21):
            records.append({
                "Compound": "MEDIUM",
                "tyre_age": age,
                "corrected_delta_s": round(0.05 * age + np.random.normal(0, 0.02), 3),
                "stint_track_temp_c": 35.0 + stint_offset,
                "source_year": 2023,
            })
    historical_deltas = pd.DataFrame(records)

    result = service._build_curves(
        empty_deltas,
        race_track_temp=35.0,
        historical_deltas=historical_deltas,
    )

    # Should return empty — historical-only compounds are skipped
    assert result == {}, (
        f"Expected empty dict for historical-only data, got {list(result.keys())}"
    )
    print("\n  Correctly skipped historical-only compounds (empty result)")


