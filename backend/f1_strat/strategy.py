"""
Race strategy engine.

Simulates full races using tyre degradation data to find the fastest pit
stop strategy.  Given a race length and degradation curves from practice,
it generates all reasonable 1-stop, 2-stop, and 3-stop strategies,
optimizes the pit lap for each one, and ranks them by total predicted
race time.

The core simulation works lap-by-lap:

    lap_time = base_lap_time
             + compound_offset                                # inherent pace gap
             + (linear × tyre_age + quadratic × tyre_age²)  # tyres wearing out
             - (fuel_correction × laps_completed)             # car getting lighter

When practice data has enough points (≥10), the degradation uses a degree-2
polynomial fit (quadratic model).  Real tyres typically show slightly concave
degradation (flattening over time), so the quadratic term is usually negative.
This makes long stints less penalized than a pure linear model, reducing the
engine's bias toward over-stopping.  When insufficient data exists, the
quadratic coefficient is 0 and behavior is identical to the previous linear model.

Each pit stop adds a fixed time penalty (typically ~22 seconds, varies
by circuit — shorter at fast tracks like Monza, longer at tight tracks
like Monaco).

Strategies are filtered to comply with FIA tyre regulations, which
require drivers to use at least two different dry compounds.  Some
events (e.g. Monaco 2025) have additional rules like mandatory two
pit stops.
"""

import logging
from itertools import product

from f1_strat.degradation import DegradationService
from f1_strat.session_service import SessionService

logger = logging.getLogger(__name__)

# Maximum number of pit stops the engine can generate strategies for.
# 3-stop strategies do happen (e.g., Japan 2024: M→M→M→H, Qatar 2024:
# M→H→H→H), so the engine supports up to 3.  The API defaults to
# max_stops=2 for speed since 3-stop optimization is O(N³).
_MAX_STOPS = 3

# Minimum laps per stint.  Real races sometimes have 3-4 lap stints
# (aggressive undercuts, late safety car restarts).  Validation showed
# _MIN_STINT_LAPS=5 was too restrictive — lowered to 4.
_MIN_STINT_LAPS = 4

# Tyre set allocation limits per race weekend.
# Each driver receives a fixed number of each compound for the whole weekend
# (practice, qualifying, and race).  The race allocation is what matters here:
# drivers typically save 2 HARD sets, 2–3 MEDIUM sets, and 1–2 SOFT sets for
# the race.  We use the maximum possible race sets as a constraint.
# This prevents the engine from generating unrealistic strategies like
# HARD→HARD→HARD→MEDIUM (would require 3 hard sets, but only 2 are available).
_MAX_SETS_PER_COMPOUND = {
    "HARD": 2,
    "MEDIUM": 3,
    "SOFT": 2,
    # Wet compounds aren't allocation-limited in the same way (FIA provides
    # extra sets when rain is expected), so we set a generous limit.
    "INTERMEDIATE": 4,
    "WET": 3,
}

# ---------------------------------------------------------------------------
# Track position model: first-stint compound pace bonus
#
# In real F1, softer compounds are ~0.5-1.0s/lap faster than harder ones
# when new.  This pace advantage matters most in the first stint because:
#   1. The field is bunched after the start — faster pace = clean air
#   2. Track position determines pit window order (pit first = undercut)
#   3. Being ahead means less dirty air and fewer overtakes needed
#
# Without this model, MEDIUM→HARD and HARD→MEDIUM have *identical* predicted
# race times (a mathematical property of linear degradation — the optimizer
# finds mirrored pit laps with equal total time).  This is wrong: real teams
# overwhelmingly prefer softer-first ordering (start MEDIUM, finish HARD)
# because of the track position advantage described above.
#
# We model this as a per-lap bonus applied ONLY to the first stint.
# In later stints the field is spread out and compound pace differences
# translate less directly to position.  The bonus is small enough to not
# override genuine time differences but large enough to correctly prefer
# softer-first orderings.
#
# _COMPOUND_SOFTNESS: tier number for each compound (higher = softer = faster)
# _TRACK_POSITION_PACE_S: seconds per lap of bonus per softness tier
#
# Example: MEDIUM first stint, 25 laps → 0.05 × 1 × 25 = 1.25s bonus
#          SOFT first stint, 15 laps  → 0.05 × 2 × 15 = 1.50s bonus
#
# _LAST_STINT_SOFT_PENALTY_S: one-time penalty (in seconds) added when the
# final stint uses a softer compound than the penultimate stint.  Real teams
# overwhelmingly prefer harder final stints because: (1) degradation risk is
# lower when positions are settled, (2) harder tyres give a wider pit window,
# and (3) a slow final stint can't be recovered from.  Without this penalty,
# M→H→M and M→H→H are equally fast with a linear model but M→H→M wrongly wins
# due to the first-stint bonus being mirrored in the last stint.
# ---------------------------------------------------------------------------
_COMPOUND_SOFTNESS = {
    "SOFT": 2,
    "MEDIUM": 1,
    "HARD": 0,
    "INTERMEDIATE": 0,
    "WET": 0,
}

_TRACK_POSITION_PACE_S = 0.05
_LAST_STINT_SOFT_PENALTY_S = 1.5


# ---------------------------------------------------------------------------
# FIA tyre regulations by year.
#
# The base rule (since 2007) is that drivers must use at least two different
# dry-weather compounds during a dry race.  This forces at least one pit stop.
#
# Starting in 2025, the FIA introduced event-specific rules to encourage
# more action at certain circuits.  These are defined as overrides keyed
# by event name.
#
# Each rule set contains:
#   min_compounds:  minimum number of distinct compounds that must appear
#   min_stops:      minimum number of pit stops required
#   max_stint_laps: maximum laps allowed on a single set of tyres (None = no limit)
# ---------------------------------------------------------------------------
_TYRE_RULES: dict = {
    # Default rules that apply to every dry race (2023+).
    # Must use at least 2 different dry compounds → forces at least 1 stop.
    "default": {
        "min_compounds": 2,
        "min_stops": 1,
        "max_stint_laps": None,
    },
    # 2025 introduced event-specific regulations.
    2025: {
        "events": {
            # Monaco: mandatory 2 pit stops (must use at least 3 sets of
            # tyres).  Still need 2 different compounds minimum.
            # Source: FIA bulletin, implemented to increase overtaking.
            "Monaco Grand Prix": {
                "min_stops": 2,
            },
            # Qatar: max 25 laps per tyre set for safety (severe tyre
            # degradation at Lusail caused concerns in 2024).  With a
            # 57-lap race this forces at least 2 pit stops.
            "Qatar Grand Prix": {
                "max_stint_laps": 25,
            },
        },
    },
}


# In wet conditions, the FIA's dry-compound diversity rule doesn't apply.
# Drivers can run a single compound (e.g., full race on intermediates).
# The min_stops override of 0 means no-stop strategies are legal
# (though event-specific rules like Monaco's 2-stop still apply on top).
_WET_OVERRIDE = {"min_compounds": 1, "min_stops": 0}

# How much slower wet tyres are compared to dry slicks (in seconds).
# These are rough averages — real gaps depend on how much water is on track.
_CONDITION_OFFSETS = {
    "dry": 0.0,
    "intermediate": 3.0,    # Inters ~3s slower than slicks
    "wet": 7.0,             # Full wets ~7s slower than slicks
}


# ---------------------------------------------------------------------------
# Weather window helpers
#
# A weather window describes a stretch of the race under one weather
# condition. Example: {"start_lap": 1, "end_lap": 19, "condition": "dry"}.
# Multiple windows define how weather changes during the race.
# ---------------------------------------------------------------------------

_VALID_CONDITIONS = {"dry", "intermediate", "wet"}

# Which tyre compounds are legal in each weather condition.
# In dry conditions: slick tyres only.  In rain: only wet compounds.
_CONDITION_COMPOUNDS = {
    "dry": {"SOFT", "MEDIUM", "HARD"},
    "intermediate": {"INTERMEDIATE"},
    "wet": {"WET", "INTERMEDIATE"},
}


def _validate_weather_windows(windows: list[dict], race_laps: int) -> None:
    """Ensure weather windows cover the full race with no gaps or overlaps.

    Each window is a dict with start_lap, end_lap, and condition.
    They must tile laps 1 through race_laps exactly.

    Raises:
        ValueError: If windows are missing, have gaps/overlaps, or
            contain invalid conditions.
    """
    if not windows:
        raise ValueError("weather_windows must contain at least one window")

    # Sort by start_lap so we can check adjacency
    sorted_wins = sorted(windows, key=lambda w: w["start_lap"])

    # First window must start on lap 1
    if sorted_wins[0]["start_lap"] != 1:
        raise ValueError(
            f"First weather window must start on lap 1, "
            f"got {sorted_wins[0]['start_lap']}"
        )

    # Last window must end on the final race lap
    if sorted_wins[-1]["end_lap"] != race_laps:
        raise ValueError(
            f"Last weather window must end on lap {race_laps}, "
            f"got {sorted_wins[-1]['end_lap']}"
        )

    # Check each window is valid and adjacent to the next
    for i, win in enumerate(sorted_wins):
        # Validate condition
        if win["condition"] not in _VALID_CONDITIONS:
            raise ValueError(
                f"Invalid condition '{win['condition']}' in window {i+1}. "
                f"Must be one of: {sorted(_VALID_CONDITIONS)}"
            )

        # end_lap must be >= start_lap
        if win["end_lap"] < win["start_lap"]:
            raise ValueError(
                f"Window {i+1}: end_lap ({win['end_lap']}) < "
                f"start_lap ({win['start_lap']})"
            )

        # Check adjacency with the next window (no gap, no overlap)
        if i < len(sorted_wins) - 1:
            next_win = sorted_wins[i + 1]
            expected_next_start = win["end_lap"] + 1
            if next_win["start_lap"] != expected_next_start:
                raise ValueError(
                    f"Gap or overlap between windows {i+1} and {i+2}: "
                    f"window {i+1} ends at lap {win['end_lap']}, "
                    f"window {i+2} starts at lap {next_win['start_lap']} "
                    f"(expected {expected_next_start})"
                )


def _get_condition_for_lap(lap: int, windows: list[dict]) -> str:
    """Look up the weather condition for a specific lap number.

    Args:
        lap: 1-indexed race lap number.
        windows: Validated list of weather window dicts.

    Returns:
        Condition string ('dry', 'intermediate', 'wet').

    Raises:
        ValueError: If the lap isn't covered by any window
            (shouldn't happen with validated windows).
    """
    for win in windows:
        if win["start_lap"] <= lap <= win["end_lap"]:
            return win["condition"]
    raise ValueError(f"Lap {lap} not covered by any weather window")


def get_tyre_rules(year: int, event_name: str, conditions: str = "dry") -> dict:
    """Look up the FIA tyre regulations for a specific race.

    Starts from the default rules and applies year + event overrides.
    In non-dry conditions, the dry-compound diversity rule is relaxed:
    drivers can run a single wet compound for the whole race.

    This is a module-level function so tests can call it directly.

    Args:
        year: Season year (e.g. 2025).
        event_name: The event name as returned by FastF1
            (e.g. 'Monaco Grand Prix').
        conditions: One of 'dry', 'intermediate', 'wet'.

    Returns:
        Dict with keys: min_compounds, min_stops, max_stint_laps.
    """
    # Start with defaults (applies to all modern F1 seasons)
    rules = dict(_TYRE_RULES["default"])

    # In wet conditions, override the dry-compound rules first.
    # Event-specific rules (e.g., Monaco 2-stop) apply on top, so a
    # Monaco wet race still requires 2 stops.
    if conditions != "dry":
        rules.update(_WET_OVERRIDE)

    # Apply year-specific event overrides if they exist
    year_rules = _TYRE_RULES.get(year, {})
    event_overrides = year_rules.get("events", {})

    # Check for an exact match on event name
    if event_name in event_overrides:
        rules.update(event_overrides[event_name])

    return rules


class StrategyEngine:
    """Generate and rank pit stop strategies for a given race."""

    def __init__(self):
        self._degradation_service = DegradationService()
        self._session_service = SessionService()

    def calculate(
        self,
        year: int,
        grand_prix: str | int,
        race_laps: int,
        pit_stop_loss_s: float = 22.0,
        fuel_correction_s: float = 0.07,
        conditions: str = "dry",
        intermediate_deg_rate: float = 0.12,
        wet_deg_rate: float = 0.15,
        weather_windows: list[dict] | None = None,
        max_stops: int = 3,
        min_stint_laps: int = _MIN_STINT_LAPS,
        tyre_warmup_loss_s: float = 1.5,
        position_loss_s: float = 3.0,
        starting_compound: str | None = None,
        deg_scaling: float = 0.85,
    ) -> dict:
        """Calculate optimal strategies for a race.

        In dry mode, only generates strategies using SOFT/MEDIUM/HARD
        with the standard FIA compound diversity rule.

        In intermediate or wet mode, uses wet-weather compounds with
        relaxed regulations (single compound allowed, 0-stop legal).
        Deg rates come from real practice data if available, otherwise
        fall back to the user-provided defaults.

        When weather_windows is provided, runs a mixed-condition simulation:
        different parts of the race use different compounds and conditions.
        Pit stops are mandatory at weather transitions, plus optional
        additional stops within each window.

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.
            race_laps: Total number of race laps.
            pit_stop_loss_s: Time lost per pit stop in seconds.
                ~22s is average; Monaco ~25s, Monza ~20s.
            fuel_correction_s: Seconds per lap the car gets faster as
                fuel burns off.  Default 0.07 matches the degradation module.
            conditions: Race conditions — 'dry', 'intermediate', or 'wet'.
                Ignored when weather_windows is provided.
            intermediate_deg_rate: Default deg rate (s/lap) for INTERMEDIATE
                compound, used when no real practice data is available.
            wet_deg_rate: Default deg rate (s/lap) for WET compound,
                used when no real practice data is available.
            weather_windows: Optional list of weather window dicts, each
                with start_lap, end_lap, condition.  When provided, overrides
                the uniform `conditions` parameter.
            max_stops: Maximum pit stops to consider.  Default 3.
                3-stop optimization is O(N³) so it's noticeably slower
                than 2-stop.  The position_loss_s penalty naturally
                discourages over-stopping, so the cap is mainly a
                performance knob.
            min_stint_laps: Minimum laps per stint.  Default 4.  Lower
                values allow shorter undercut stints but increase search
                space.
            tyre_warmup_loss_s: Extra seconds lost on the first lap of
                each stint after a pit stop, due to cold tyres needing
                ~1 lap to reach operating temperature.  Default 1.5s.
                This is on top of pit_stop_loss_s and makes additional
                pit stops slightly more expensive.
            position_loss_s: Seconds of escalating penalty per pit stop
                for losing track position and encountering traffic.
                Stop 1 costs 1×, stop 2 costs 2×, stop 3 costs 3×.
                Total = position_loss_s × N × (N+1) / 2.
                Default 3.0s.  This naturally prevents over-stopping
                without needing an artificial max_stops cap.
            starting_compound: If set, only generate strategies that
                start on this compound.  Used for top-10 qualifiers
                who must start on their Q2 fastest-lap compound.
            deg_scaling: Multiplier applied to practice-derived deg rates
                to account for the gap between practice and race conditions.
                Default 0.85 (15% reduction).  Race conditions have more
                rubber on track, cooler track temps (sometimes), and
                different car setups, all of which reduce real degradation
                vs what's measured in practice.  Set to 1.0 to disable.

        Returns:
            Dict with event info, applied regulations, conditions used,
            actual deg rates, and a ranked list of legal strategies.
        """
        # -- Step 1: Get degradation data from practice --------------------
        deg_data = self._degradation_service.analyze(
            year, grand_prix, fuel_correction_s
        )
        event_name = deg_data["event_name"]

        # -- Mixed weather path: delegate to the weather-aware engine ------
        if weather_windows is not None:
            return self._calculate_weather(
                year, grand_prix, race_laps, pit_stop_loss_s,
                fuel_correction_s, intermediate_deg_rate, wet_deg_rate,
                weather_windows, deg_data, event_name,
                tyre_warmup_loss_s, position_loss_s, deg_scaling,
            )

        # -- Uniform conditions path (existing behavior) -------------------

        # -- Step 1b: Look up the tyre regulations for this race -----------
        rules = get_tyre_rules(year, event_name, conditions)
        logger.info(
            "Tyre rules for %s %s (%s): min_compounds=%d, min_stops=%d, "
            "max_stint_laps=%s",
            year, event_name, conditions,
            rules["min_compounds"], rules["min_stops"],
            rules["max_stint_laps"],
        )

        # -- Step 1c: Build deg rates based on conditions ------------------
        deg_rates, base_time_offset, compound_offsets = self._get_compound_config(
            conditions, deg_data, year, grand_prix, fuel_correction_s,
            intermediate_deg_rate, wet_deg_rate, deg_scaling,
            starting_compound,
        )

        if not deg_rates:
            return {
                "event_name": event_name,
                "year": year,
                "race_laps": race_laps,
                "pit_stop_loss_s": pit_stop_loss_s,
                "base_lap_time_s": None,
                "regulations": rules,
                "conditions": conditions,
                "deg_rates_used": {},
                "strategies": [],
            }

        # -- Step 2: Get the base lap time ---------------------------------
        base_lap_time = self._session_service.get_base_lap_time(year, grand_prix)

        # Apply the wet-weather offset to the base lap time.
        # Intermediates are ~3s slower than slicks, full wets ~7s slower.
        adjusted_base = base_lap_time + base_time_offset

        # -- Step 3: Generate legal compound sequences to evaluate ---------
        available = sorted(deg_rates.keys())
        sequences = self._generate_sequences(
            available, race_laps, rules, max_stops, min_stint_laps,
            starting_compound,
        )

        # -- Step 4: For each sequence, find optimal pit laps and time -----
        strategies = []
        for compounds in sequences:
            result = self._optimize_strategy(
                compounds, race_laps, adjusted_base,
                deg_rates, fuel_correction_s, pit_stop_loss_s, rules,
                min_stint_laps, tyre_warmup_loss_s, position_loss_s,
                compound_offsets,
            )
            if result is not None:
                strategies.append(result)

        # -- Step 5: Rank by total time ------------------------------------
        strategies.sort(key=lambda s: s["total_time_s"])

        if strategies:
            best_time = strategies[0]["total_time_s"]
            for i, strat in enumerate(strategies):
                strat["rank"] = i + 1
                strat["gap_to_best_s"] = round(strat["total_time_s"] - best_time, 1)

        # Convert deg_rates back to flat {compound: float} for the API
        # output.  The internal dict-of-dicts format is an implementation
        # detail — the API consumer only needs the linear rate for display.
        flat_deg_rates = {
            compound: coeffs["linear"]
            for compound, coeffs in deg_rates.items()
        }

        result = {
            "event_name": event_name,
            "year": year,
            "race_laps": race_laps,
            "pit_stop_loss_s": pit_stop_loss_s,
            "base_lap_time_s": base_lap_time,
            "regulations": rules,
            "conditions": conditions,
            "deg_rates_used": flat_deg_rates,
            "compound_offsets_used": compound_offsets,
            "strategies": strategies,
        }

        # Include starting_compound in the response so the frontend and
        # validation can see what constraint was applied
        if starting_compound:
            result["starting_compound"] = starting_compound

        return result

    # ------------------------------------------------------------------
    # Mixed-weather strategy calculation
    # ------------------------------------------------------------------

    def _calculate_weather(
        self,
        year: int,
        grand_prix: str | int,
        race_laps: int,
        pit_stop_loss_s: float,
        fuel_correction_s: float,
        intermediate_deg_rate: float,
        wet_deg_rate: float,
        weather_windows: list[dict],
        deg_data: dict,
        event_name: str,
        tyre_warmup_loss_s: float = 0.0,
        position_loss_s: float = 0.0,
        deg_scaling: float = 0.85,
    ) -> dict:
        """Calculate strategies for a race with changing weather.

        This is the mixed-weather counterpart to the main calculate() path.
        It generates strategies that respect weather boundaries: drivers must
        pit at weather transitions to switch between dry and wet compounds.

        The approach:
        1. Validate weather windows cover the full race
        2. Build deg rates for ALL conditions present (dry + wet)
        3. Generate compound sequences with mandatory pits at transitions
        4. Optimize pit laps within each weather window
        5. Rank by total time
        """
        _validate_weather_windows(weather_windows, race_laps)

        # Sort windows by start_lap for consistent processing
        sorted_windows = sorted(weather_windows, key=lambda w: w["start_lap"])

        # -- Build deg rates for every condition present -------------------
        # We need both dry and wet rates to simulate mixed conditions.
        # Dict-of-dicts format: {compound: {"linear": float, "quadratic": float}}
        all_deg_rates = {}

        # Dry rates from practice, scaled to race conditions
        for compound, info in deg_data["compounds"].items():
            if "deg_coefficients" in info:
                coeffs = info["deg_coefficients"]
                all_deg_rates[compound] = {
                    "linear": round(coeffs["linear"] * deg_scaling, 4),
                    "quadratic": round(coeffs["quadratic"] * deg_scaling, 6),
                }
            else:
                rate = round(info["degradation_per_lap_s"] * deg_scaling, 4)
                all_deg_rates[compound] = {"linear": rate, "quadratic": 0.0}

        # Wet rates: try real data first, fall back to defaults.
        # Wet compounds always use quadratic=0 (too little data).
        wet_data = self._degradation_service.analyze_wet(
            year, grand_prix, fuel_correction_s
        )
        real_wet_rates = {
            compound: info["degradation_per_lap_s"]
            for compound, info in wet_data["compounds"].items()
        }
        all_deg_rates["INTERMEDIATE"] = {
            "linear": real_wet_rates.get("INTERMEDIATE", intermediate_deg_rate),
            "quadratic": 0.0,
        }
        all_deg_rates["WET"] = {
            "linear": real_wet_rates.get("WET", wet_deg_rate),
            "quadratic": 0.0,
        }

        # -- Compound base pace offsets (dry compounds only) -----------------
        compound_offsets = dict(deg_data.get("compound_offsets", {}))

        # -- Base lap time (dry) -------------------------------------------
        base_lap_time = self._session_service.get_base_lap_time(year, grand_prix)

        # -- Generate weather-aware compound sequences ---------------------
        sequences = self._generate_weather_sequences(sorted_windows)

        # -- Optimize each sequence ----------------------------------------
        strategies = []
        for compounds_per_window in sequences:
            result = self._optimize_weather_strategy(
                compounds_per_window, sorted_windows, race_laps,
                base_lap_time, all_deg_rates, fuel_correction_s,
                pit_stop_loss_s, tyre_warmup_loss_s, position_loss_s,
                compound_offsets,
            )
            if result is not None:
                strategies.append(result)

        # -- Rank by total time --------------------------------------------
        strategies.sort(key=lambda s: s["total_time_s"])

        if strategies:
            best_time = strategies[0]["total_time_s"]
            for i, strat in enumerate(strategies):
                strat["rank"] = i + 1
                strat["gap_to_best_s"] = round(
                    strat["total_time_s"] - best_time, 1
                )

        # Use "dry" rules as baseline for the regulations summary
        rules = get_tyre_rules(year, event_name, "dry")

        # Convert to flat format for the API output
        flat_deg_rates = {
            compound: coeffs["linear"]
            for compound, coeffs in all_deg_rates.items()
        }

        return {
            "event_name": event_name,
            "year": year,
            "race_laps": race_laps,
            "pit_stop_loss_s": pit_stop_loss_s,
            "base_lap_time_s": base_lap_time,
            "regulations": rules,
            "conditions": "mixed",
            "deg_rates_used": flat_deg_rates,
            "compound_offsets_used": compound_offsets,
            "weather_windows": sorted_windows,
            "strategies": strategies,
        }

    def _generate_weather_sequences(
        self,
        windows: list[dict],
    ) -> list[list[list[str]]]:
        """Generate compound options for each weather window.

        Each window gets a list of legal compounds for its condition.
        Within a window, we allow either 1 stint (no extra stop) or
        2 stints (one extra stop) — keeping total stints manageable.

        Returns a list of "per-window compound plans".  Each plan is a
        list of lists: plan[window_index] = [compound1, compound2, ...].
        For example, a dry-rain-dry race might produce:
          [["MEDIUM", "HARD"], ["INTERMEDIATE"], ["MEDIUM"]]
        meaning: 2 dry stints, 1 inter stint, 1 dry stint = 4 total stints.
        """
        # For each window, figure out the possible compound combos
        per_window_options: list[list[list[str]]] = []
        for win in windows:
            condition = win["condition"]
            legal = sorted(_CONDITION_COMPOUNDS[condition])
            window_laps = win["end_lap"] - win["start_lap"] + 1

            combos: list[list[str]] = []

            # Option A: 1 stint for this window (single compound)
            for compound in legal:
                combos.append([compound])

            # Option B: 2 stints for this window (one extra stop within),
            # but only if the window is long enough for 2 stints
            if window_laps >= 2 * _MIN_STINT_LAPS:
                for c1 in legal:
                    for c2 in legal:
                        combos.append([c1, c2])

            per_window_options.append(combos)

        # Combine options across all windows using itertools.product.
        # This gives us every combination of compound choices per window.
        all_plans = list(product(*per_window_options))
        return [list(plan) for plan in all_plans]

    def _optimize_weather_strategy(
        self,
        compounds_per_window: list[list[str]],
        windows: list[dict],
        race_laps: int,
        base_lap_time: float,
        deg_rates: dict[str, dict],
        fuel_correction_s: float,
        pit_stop_loss_s: float,
        tyre_warmup_loss_s: float = 0.0,
        position_loss_s: float = 0.0,
        compound_offsets: dict[str, float] | None = None,
    ) -> dict | None:
        """Find optimal pit laps for a weather-aware compound plan.

        Pit stops are mandatory at weather transitions (last lap of each
        window except the last one).  Within each window, if there are
        2 stints, we brute-force the best split point.

        The key insight: we can optimize each window independently because
        the transition pits are fixed.  This keeps the search space small:
        O(window_laps) per multi-stint window, instead of O(N^2) across
        the whole race.
        """
        # Build the full stint list by optimizing within each window
        all_stints: list[dict] = []
        total_time = 0.0
        race_lap_cursor = 1

        for win_idx, (win, compounds) in enumerate(
            zip(windows, compounds_per_window)
        ):
            window_laps = win["end_lap"] - win["start_lap"] + 1

            if len(compounds) == 1:
                # Single stint for this window — no optimization needed
                all_stints.append({
                    "compound": compounds[0],
                    "start_lap": win["start_lap"],
                    "end_lap": win["end_lap"],
                    "laps": window_laps,
                    "condition": win["condition"],
                })

            elif len(compounds) == 2:
                # Two stints — find the best split point within this window
                best_split = None
                best_split_time = float("inf")

                for split_at in range(
                    _MIN_STINT_LAPS,
                    window_laps - _MIN_STINT_LAPS + 1,
                ):
                    stint1_laps = split_at
                    stint2_laps = window_laps - split_at

                    # Simulate just this window's contribution to total time
                    # by computing the two stints' lap times
                    window_time = self._simulate_window(
                        compounds, [stint1_laps, stint2_laps],
                        base_lap_time, deg_rates, fuel_correction_s,
                        race_laps, win["start_lap"], win["condition"],
                        compound_offsets,
                    )
                    # Add one pit stop within the window
                    window_time += pit_stop_loss_s

                    if window_time < best_split_time:
                        best_split_time = window_time
                        best_split = (stint1_laps, stint2_laps)

                if best_split is None:
                    return None  # Window too short for 2 stints

                s1_laps, s2_laps = best_split
                all_stints.append({
                    "compound": compounds[0],
                    "start_lap": win["start_lap"],
                    "end_lap": win["start_lap"] + s1_laps - 1,
                    "laps": s1_laps,
                    "condition": win["condition"],
                })
                all_stints.append({
                    "compound": compounds[1],
                    "start_lap": win["start_lap"] + s1_laps,
                    "end_lap": win["end_lap"],
                    "laps": s2_laps,
                    "condition": win["condition"],
                })

        # Now simulate the full race with this stint plan
        flat_compounds = tuple(s["compound"] for s in all_stints)
        flat_lengths = [s["laps"] for s in all_stints]

        total_time = self._simulate_race(
            flat_compounds, flat_lengths, base_lap_time,
            deg_rates, fuel_correction_s, pit_stop_loss_s,
            race_laps, weather_windows=windows,
            tyre_warmup_loss_s=tyre_warmup_loss_s,
            position_loss_s=position_loss_s,
            compound_offsets=compound_offsets,
        )

        # Subtract the pit stops that _simulate_race added (it counts all
        # stint boundaries), then add back only the correct count.
        # _simulate_race adds (num_stints - 1) pit stops, but weather
        # transition pits are also real stops, so this is actually correct.
        # No adjustment needed — every stint boundary IS a pit stop.

        num_stops = len(all_stints) - 1
        # Build a readable name showing compound flow across weather
        parts = []
        for stint in all_stints:
            parts.append(stint["compound"])
        name = f"{num_stops}-stop: {' → '.join(parts)}"

        return {
            "name": name,
            "total_time_s": round(total_time, 1),
            "num_stops": num_stops,
            "stints": all_stints,
        }

    def _simulate_window(
        self,
        compounds: list[str],
        stint_lengths: list[int],
        base_lap_time: float,
        deg_rates: dict[str, dict],
        fuel_correction_s: float,
        race_laps: int,
        window_start_lap: int,
        condition: str,
        compound_offsets: dict[str, float] | None = None,
    ) -> float:
        """Simulate a subset of the race within one weather window.

        Used during optimization to compare different split points within
        a single weather window.  Returns the time for just these stints
        (no pit stop penalties — those are added by the caller).
        """
        time = 0.0
        race_lap = window_start_lap
        condition_offset = _CONDITION_OFFSETS.get(condition, 0.0)

        for compound, stint_laps in zip(compounds, stint_lengths):
            coeffs = deg_rates[compound]
            # Compound base pace offset — inherent speed gap between
            # compounds on fresh tyres (e.g., SOFT faster than HARD)
            cpd_offset = 0.0
            if compound_offsets:
                cpd_offset = compound_offsets.get(compound, 0.0)

            for tyre_age in range(1, stint_laps + 1):
                laps_remaining = race_laps - race_lap + 1
                lap_time = (
                    base_lap_time
                    + cpd_offset
                    + (coeffs["linear"] * tyre_age
                       + coeffs["quadratic"] * tyre_age ** 2)
                    - (fuel_correction_s * (race_laps - laps_remaining))
                    + condition_offset
                )
                time += lap_time
                race_lap += 1

        return time

    def _get_compound_config(
        self,
        conditions: str,
        deg_data: dict,
        year: int,
        grand_prix: str | int,
        fuel_correction_s: float,
        intermediate_deg_rate: float,
        wet_deg_rate: float,
        deg_scaling: float = 0.85,
        starting_compound: str | None = None,
    ) -> tuple[dict[str, dict], float, dict[str, float]]:
        """Build compound deg rates and base time offset for the given conditions.

        In dry mode, uses the practice-derived dry compound rates, scaled
        by ``deg_scaling`` to account for practice-vs-race differences
        (track evolution, rubber build-up, car mode).

        In intermediate/wet mode, tries to use real practice data from
        analyze_wet() first.  If no wet practice data exists (the usual case),
        falls back to the user-provided default rates.

        Returns:
            (deg_rates_dict, base_time_offset_s, compound_offsets) where:
            - deg_rates_dict maps compound names to
              {"linear": float, "quadratic": float}
            - base_time_offset_s is the condition-based time offset
            - compound_offsets maps compound names to base pace offset
              in seconds (0.0 for fastest, positive for slower compounds)
        """
        base_time_offset = _CONDITION_OFFSETS.get(conditions, 0.0)

        if conditions == "dry":
            # Use dry compound rates from practice, scaled to race conditions.
            # Practice data systematically overestimates race degradation
            # because the track has less rubber laid down and different temps.
            #
            # deg_rates is now a dict of dicts: each compound maps to
            # {"linear": float, "quadratic": float}.  The strategy engine
            # uses both coefficients for more accurate lap simulation.
            # deg_scaling applies to BOTH coefficients so the scaling is
            # consistent (if practice over-estimates by 15%, both the
            # linear and quadratic terms are equally inflated).
            deg_rates = {}
            for compound, info in deg_data["compounds"].items():
                if "deg_coefficients" in info:
                    coeffs = info["deg_coefficients"]
                    deg_rates[compound] = {
                        "linear": round(coeffs["linear"] * deg_scaling, 4),
                        "quadratic": round(coeffs["quadratic"] * deg_scaling, 6),
                    }
                else:
                    # Backward compatibility: if degradation data doesn't have
                    # the new coefficients (e.g., old cached data), fall back
                    # to the linear-only rate with quadratic=0.
                    rate = round(info["degradation_per_lap_s"] * deg_scaling, 4)
                    deg_rates[compound] = {"linear": rate, "quadratic": 0.0}

            # -- Compound base pace offsets from practice -------------------
            # Fresh SOFT tyres are ~1s/lap faster than fresh HARD tyres.
            # These offsets capture that inherent pace gap, independent of
            # degradation rate.  NOT scaled by deg_scaling because they
            # represent inherent compound pace, not degradation.
            compound_offsets = dict(deg_data.get("compound_offsets", {}))

            # Fallback: if HARD data is missing from practice (teams often
            # don't do long HARD runs in practice), estimate it from the
            # MEDIUM rate.  HARD typically degrades at ~60% the rate of
            # MEDIUM.  This ensures HARD strategies are always available,
            # since MEDIUM→HARD is the most common winning strategy in F1.
            # Both coefficients are scaled by 60%.
            if "HARD" not in deg_rates and "MEDIUM" in deg_rates:
                med = deg_rates["MEDIUM"]
                deg_rates["HARD"] = {
                    "linear": round(max(med["linear"] * 0.6, 0.02), 4),
                    "quadratic": round(med["quadratic"] * 0.6, 6),
                }
                logger.info(
                    "No HARD deg data from practice — estimating linear=%.4f "
                    "quad=%.6f (60%% of MEDIUM)",
                    deg_rates["HARD"]["linear"], deg_rates["HARD"]["quadratic"],
                )
                # Fallback offset for HARD: MEDIUM offset + 0.6s.
                # Community consensus: HARD is ~0.5-1.0s slower than MEDIUM
                # on fresh tyres.  0.6s is conservative.
                if "HARD" not in compound_offsets and "MEDIUM" in compound_offsets:
                    compound_offsets["HARD"] = round(
                        compound_offsets["MEDIUM"] + 0.6, 3
                    )

            # Fallback: if SOFT data is missing from practice AND the user
            # explicitly requested SOFT as a starting compound (e.g., Q2
            # tyre rule), estimate it from the MEDIUM rate.  SOFT typically
            # degrades at ~160% the rate of MEDIUM.  We only do this when
            # SOFT is explicitly needed because estimated rates are noisy
            # and can make SOFT strategies look artificially competitive.
            if (
                "SOFT" not in deg_rates
                and "MEDIUM" in deg_rates
                and starting_compound == "SOFT"
            ):
                med = deg_rates["MEDIUM"]
                deg_rates["SOFT"] = {
                    "linear": round(max(med["linear"] * 1.6, 0.05), 4),
                    "quadratic": round(med["quadratic"] * 1.6, 6),
                }
                logger.info(
                    "No SOFT deg data from practice — estimating linear=%.4f "
                    "quad=%.6f (160%% of MEDIUM, needed for starting_compound=SOFT)",
                    deg_rates["SOFT"]["linear"], deg_rates["SOFT"]["quadratic"],
                )
                # Fallback offset for SOFT: max(0, MEDIUM offset - 0.5s).
                # SOFT is faster than MEDIUM, so its offset should be lower.
                if "SOFT" not in compound_offsets and "MEDIUM" in compound_offsets:
                    compound_offsets["SOFT"] = round(
                        max(0.0, compound_offsets["MEDIUM"] - 0.5), 3
                    )

            return deg_rates, base_time_offset, compound_offsets

        # -- Wet conditions: try real practice data first ------------------
        # Wet compounds always use quadratic=0 because there's too little
        # practice data in the wet to fit a reliable quadratic curve.
        wet_data = self._degradation_service.analyze_wet(
            year, grand_prix, fuel_correction_s
        )
        real_wet_rates = {
            compound: info["degradation_per_lap_s"]
            for compound, info in wet_data["compounds"].items()
        }

        if conditions == "intermediate":
            rate = real_wet_rates.get("INTERMEDIATE", intermediate_deg_rate)
            deg_rates = {
                "INTERMEDIATE": {"linear": rate, "quadratic": 0.0},
            }
        else:
            wet_rate = real_wet_rates.get("WET", wet_deg_rate)
            inter_rate = real_wet_rates.get("INTERMEDIATE", intermediate_deg_rate)
            deg_rates = {
                "WET": {"linear": wet_rate, "quadratic": 0.0},
                "INTERMEDIATE": {"linear": inter_rate, "quadratic": 0.0},
            }

        # Wet compounds don't have meaningful base pace offsets — they're
        # a completely different class of tyre with different baseline speed.
        # The condition offset (3s/7s) already handles the pace difference.
        return deg_rates, base_time_offset, {}

    # ------------------------------------------------------------------
    # Strategy generation
    # ------------------------------------------------------------------

    def _generate_sequences(
        self,
        compounds: list[str],
        race_laps: int,
        rules: dict,
        max_stops: int = 3,
        min_stint_laps: int = _MIN_STINT_LAPS,
        starting_compound: str | None = None,
    ) -> list[tuple[str, ...]]:
        """Generate all regulation-legal compound sequences to evaluate.

        Creates permutations of compounds for 0-stop through max_stops
        strategies, then filters out any that violate the FIA tyre
        regulations:

        1. Must use at least `min_compounds` distinct compounds
           (standard dry rule: 2 → no HARD→HARD or SOFT→SOFT→SOFT)
        2. Must have at least `min_stops` pit stops
           (Monaco 2025: min_stops=2 → no 1-stop strategies)

        0-stop strategies (full race on one compound) are only legal
        when min_stops=0, which happens in wet conditions.

        Allows legal compound repeats (e.g., SOFT→HARD→SOFT) because
        sometimes splitting a high-deg compound across two shorter stints
        is faster than a single long degraded stint.  Repeats are limited
        by tyre set allocation (_MAX_SETS_PER_COMPOUND) — e.g., you can't
        use HARD three times because teams only have 2 hard sets available
        for the race.

        Args:
            compounds: Available compound names (e.g., ['HARD','MEDIUM','SOFT']).
            race_laps: Total laps in the race.
            rules: FIA tyre rules dict (min_compounds, min_stops, max_stint_laps).
            max_stops: Maximum pit stops to generate strategies for.
            min_stint_laps: Minimum laps per stint.
            starting_compound: If set, only keep sequences that start with
                this compound.  Used for the Q2 tyre rule (top-10 qualifiers
                must start on their Q2 fastest-lap compound).
        """
        min_compounds = rules["min_compounds"]
        min_stops = rules["min_stops"]
        max_stint = rules["max_stint_laps"]
        sequences = []

        def _within_tyre_allocation(combo: tuple[str, ...]) -> bool:
            """Check that no compound is used more times than sets available."""
            from collections import Counter
            counts = Counter(combo)
            for compound, count in counts.items():
                max_sets = _MAX_SETS_PER_COMPOUND.get(compound, 2)
                if count > max_sets:
                    return False
            return True

        # 0-stop strategies: 1 stint (full race on one compound).
        # Only legal when min_stops=0 (wet conditions) and no max_stint
        # limit would prevent it.
        if min_stops == 0 and max_stops >= 0:
            for compound in compounds:
                # Skip if the race is longer than the max stint allows
                if max_stint and race_laps > max_stint:
                    continue
                sequences.append((compound,))

        # 1-stop strategies: 2 stints — only if regulations allow 1 stop
        if max_stops >= 1 and min_stops <= 1 and race_laps >= min_stint_laps * 2:
            for combo in product(compounds, repeat=2):
                # Enforce minimum compound variety (e.g., no HARD→HARD)
                if len(set(combo)) >= min_compounds:
                    sequences.append(combo)

        # 2-stop strategies: 3 stints
        if max_stops >= 2 and race_laps >= min_stint_laps * 3:
            for combo in product(compounds, repeat=3):
                if len(set(combo)) >= min_compounds and _within_tyre_allocation(combo):
                    sequences.append(combo)

        # 3-stop strategies: 4 stints.
        # These are uncommon but do happen at high-degradation circuits
        # (e.g., Japan 2024: M→M→M→H, Qatar 2024: M→H→H→H).
        # Only generated when max_stops >= 3 because the O(N³) optimization
        # for 4 stints is significantly slower than 2-stop or 3-stop.
        if max_stops >= 3 and race_laps >= min_stint_laps * 4:
            for combo in product(compounds, repeat=4):
                if len(set(combo)) >= min_compounds and _within_tyre_allocation(combo):
                    sequences.append(combo)

        # Q2 tyre rule: top-10 qualifiers must start on their Q2 fastest-lap
        # compound.  Filter to only sequences beginning with that compound.
        if starting_compound:
            sequences = [s for s in sequences if s[0] == starting_compound]

        return sequences

    # ------------------------------------------------------------------
    # Optimization: find the best pit lap(s) for a compound sequence
    # ------------------------------------------------------------------

    def _optimize_strategy(
        self,
        compounds: tuple[str, ...],
        race_laps: int,
        base_lap_time: float,
        deg_rates: dict[str, dict],
        fuel_correction_s: float,
        pit_stop_loss_s: float,
        rules: dict,
        min_stint_laps: int = _MIN_STINT_LAPS,
        tyre_warmup_loss_s: float = 0.0,
        position_loss_s: float = 0.0,
        compound_offsets: dict[str, float] | None = None,
    ) -> dict | None:
        """Find the optimal pit lap(s) for a given compound sequence.

        Tries all valid ways to split the race into stints and picks the
        split that gives the lowest total time.  Respects max_stint_laps
        if set by regulations (e.g., Qatar 2025: max 25 laps per set).

        For 3-stop (4 stints), uses a triple-nested loop which is O(N³).
        With min_stint_laps=4 and a 66-lap race, this is ~20K combinations
        per compound sequence — feasible but noticeably slower than 2-stop.

        Returns:
            Strategy dict with name, total_time, stints, etc., or None
            if no valid split exists.
        """
        num_stints = len(compounds)
        num_stops = num_stints - 1
        max_stint = rules["max_stint_laps"]

        best_time = float("inf")
        best_splits = None

        if num_stints == 1:
            # 0-stop: full race on one compound — only in wet conditions
            splits = [race_laps]
            if max_stint and splits[0] > max_stint:
                return None
            time = self._simulate_race(
                compounds, splits, base_lap_time,
                deg_rates, fuel_correction_s, pit_stop_loss_s, race_laps,
                tyre_warmup_loss_s=tyre_warmup_loss_s,
                position_loss_s=position_loss_s,
                compound_offsets=compound_offsets,
            )
            best_time = time
            best_splits = splits

        elif num_stints == 2:
            # 1-stop: try every possible pit lap
            for pit_lap in range(min_stint_laps, race_laps - min_stint_laps + 1):
                splits = [pit_lap, race_laps - pit_lap]
                # Skip if any stint exceeds the regulatory max
                if max_stint and any(s > max_stint for s in splits):
                    continue
                time = self._simulate_race(
                    compounds, splits, base_lap_time,
                    deg_rates, fuel_correction_s, pit_stop_loss_s, race_laps,
                    tyre_warmup_loss_s=tyre_warmup_loss_s,
                    position_loss_s=position_loss_s,
                    compound_offsets=compound_offsets,
                )
                if time < best_time:
                    best_time = time
                    best_splits = splits

        elif num_stints == 3:
            # 2-stop: try all combinations of two pit laps.
            # pit1 = end of stint 1, pit2 = end of stint 2.
            for pit1 in range(min_stint_laps, race_laps - 2 * min_stint_laps + 1):
                for pit2 in range(
                    pit1 + min_stint_laps,
                    race_laps - min_stint_laps + 1,
                ):
                    splits = [pit1, pit2 - pit1, race_laps - pit2]
                    if max_stint and any(s > max_stint for s in splits):
                        continue
                    time = self._simulate_race(
                        compounds, splits, base_lap_time,
                        deg_rates, fuel_correction_s, pit_stop_loss_s, race_laps,
                        tyre_warmup_loss_s=tyre_warmup_loss_s,
                        position_loss_s=position_loss_s,
                        compound_offsets=compound_offsets,
                    )
                    if time < best_time:
                        best_time = time
                        best_splits = splits

        elif num_stints == 4:
            # 3-stop: try all combinations of three pit laps.
            # pit1 = end of stint 1, pit2 = end of stint 2, pit3 = end of stint 3.
            # This is O(N³) but feasible: ~20K combos for a 66-lap race.
            for pit1 in range(
                min_stint_laps,
                race_laps - 3 * min_stint_laps + 1,
            ):
                for pit2 in range(
                    pit1 + min_stint_laps,
                    race_laps - 2 * min_stint_laps + 1,
                ):
                    for pit3 in range(
                        pit2 + min_stint_laps,
                        race_laps - min_stint_laps + 1,
                    ):
                        splits = [
                            pit1,
                            pit2 - pit1,
                            pit3 - pit2,
                            race_laps - pit3,
                        ]
                        if max_stint and any(s > max_stint for s in splits):
                            continue
                        time = self._simulate_race(
                            compounds, splits, base_lap_time,
                            deg_rates, fuel_correction_s, pit_stop_loss_s,
                            race_laps,
                            tyre_warmup_loss_s=tyre_warmup_loss_s,
                            position_loss_s=position_loss_s,
                            compound_offsets=compound_offsets,
                        )
                        if time < best_time:
                            best_time = time
                            best_splits = splits

        if best_splits is None:
            return None

        # Build stint details for the output
        stints = []
        lap_cursor = 1
        for compound, stint_laps in zip(compounds, best_splits):
            stints.append({
                "compound": compound,
                "start_lap": lap_cursor,
                "end_lap": lap_cursor + stint_laps - 1,
                "laps": stint_laps,
            })
            lap_cursor += stint_laps

        # Build a readable name like "1-stop: MEDIUM → HARD"
        name = f"{num_stops}-stop: {' → '.join(compounds)}"

        return {
            "name": name,
            "total_time_s": round(best_time, 1),
            "num_stops": num_stops,
            "stints": stints,
            # rank and gap_to_best are filled in by calculate() after sorting
        }

    # ------------------------------------------------------------------
    # Lap-by-lap race simulation
    # ------------------------------------------------------------------

    def _simulate_race(
        self,
        compounds: tuple[str, ...],
        stint_lengths: list[int],
        base_lap_time: float,
        deg_rates: dict[str, dict],
        fuel_correction_s: float,
        pit_stop_loss_s: float,
        race_laps: int,
        weather_windows: list[dict] | None = None,
        tyre_warmup_loss_s: float = 0.0,
        position_loss_s: float = 0.0,
        compound_offsets: dict[str, float] | None = None,
    ) -> float:
        """Simulate a full race and return the total time in seconds.

        Runs through every lap, computing:
            lap_time = base + compound_offset
                     + (linear × tyre_age + quadratic × tyre_age²)
                     - (fuel_corr × laps_completed)
                     + weather_offset  (if weather_windows provided)
                     + warmup_penalty  (first lap of each stint after a pit stop)

        The compound_offset captures the inherent pace difference between
        compounds on fresh tyres (e.g., SOFT is ~1s/lap faster than HARD).
        This is NOT scaled by deg_scaling because it's inherent compound
        pace, not degradation.

        The weather_offset adds time for wet conditions on a per-lap basis.
        When weather_windows is None, lap times use the base time as-is
        (existing behavior for uniform dry/wet/intermediate modes).

        The tyre_warmup_loss_s penalty is applied on the first lap of every
        stint after the first (i.e., after each pit stop).  New tyres need
        ~1 lap to reach operating temperature, making the out-lap slower.
        This makes additional pit stops slightly more expensive, which
        corrects the model's bias toward recommending too many stops.

        The position_loss_s penalty models the escalating cost of losing
        track position with each additional pit stop.  Each successive stop
        costs more because the driver increasingly encounters traffic on
        out-laps:
            stop 1: 1 × position_loss_s
            stop 2: 2 × position_loss_s
            stop 3: 3 × position_loss_s
        Total = position_loss_s × num_stops × (num_stops + 1) / 2.
        This naturally prevents the model from over-recommending extra
        stops without needing an artificial max_stops cap.

        Args:
            compounds: Tyre compound for each stint (e.g., ('MEDIUM', 'HARD')).
            stint_lengths: Number of laps in each stint (must sum to race_laps).
            base_lap_time: Ideal lap time in seconds (fastest DRY practice lap).
            deg_rates: Degradation coefficients per compound.  Each value
                is {"linear": float, "quadratic": float}.  When quadratic=0,
                this is identical to the old linear model.
            fuel_correction_s: Time gained per lap from fuel burn-off.
            pit_stop_loss_s: Time lost per pit stop.
            race_laps: Total race laps (used for fuel calculation).
            weather_windows: Optional list of weather window dicts.  When
                provided, each lap gets a condition-based time offset
                (0s for dry, ~3s for inters, ~7s for wets).
            tyre_warmup_loss_s: Extra time on the first lap after a pit stop
                due to cold tyres.  Default 0.0 (backward compatible).
            position_loss_s: Seconds lost per escalating pit stop due to
                track position loss and traffic.  Default 0.0 (backward
                compatible).  Typical value: 3.0.
            compound_offsets: Per-compound base pace offset in seconds.
                0.0 for the fastest compound, positive for slower ones.
                Default None (no offsets applied — backward compatible).

        Returns:
            Total race time in seconds.
        """
        total_time = 0.0
        race_lap = 1  # Current race lap number (1-indexed)

        for stint_idx, (compound, stint_laps) in enumerate(
            zip(compounds, stint_lengths)
        ):
            # deg_rates is a dict-of-dicts: each compound maps to
            # {"linear": float, "quadratic": float}.
            coeffs = deg_rates[compound]

            for tyre_age in range(1, stint_laps + 1):
                # How many laps remain INCLUDING this one.
                # On the last lap (race_lap == race_laps), remaining = 1.
                laps_remaining = race_laps - race_lap + 1

                # Base time + compound offset + degradation penalty - fuel benefit.
                #
                # compound_offset: inherent pace gap between compounds on
                # fresh tyres.  SOFT is fastest (offset=0), HARD is slowest
                # (offset ~1.0s).  This is NOT degradation — it's the raw
                # speed difference from compound softness/grip level.
                #
                # Degradation uses a quadratic model:
                #   linear * tyre_age + quadratic * tyre_age²
                # When quadratic=0, this is identical to the old linear model.
                # When quadratic < 0 (typical — concave/flattening), long
                # stints are less penalized than pure linear would predict.
                offset = 0.0
                if compound_offsets:
                    offset = compound_offsets.get(compound, 0.0)

                lap_time = (
                    base_lap_time
                    + offset
                    + (coeffs["linear"] * tyre_age
                       + coeffs["quadratic"] * tyre_age ** 2)
                    - (fuel_correction_s * (race_laps - laps_remaining))
                )

                # Track position bonus in the first stint: softer compounds
                # give better early pace, which translates to a clean-air
                # and position advantage when the field is bunched.
                # See _COMPOUND_SOFTNESS / _TRACK_POSITION_PACE_S above.
                if stint_idx == 0:
                    softness = _COMPOUND_SOFTNESS.get(compound, 0)
                    lap_time -= _TRACK_POSITION_PACE_S * softness

                # Cold-tyre warm-up penalty: first lap of each stint after
                # the first.  New tyres are ~1-2s slower on their first lap
                # because they haven't reached operating temperature yet.
                # This adds a realistic cost to each pit stop beyond just
                # the pit lane time loss.
                if stint_idx > 0 and tyre_age == 1:
                    lap_time += tyre_warmup_loss_s

                # In mixed-weather mode, add a per-lap offset for wet conditions.
                # Dry laps get +0s, intermediate laps ~+3s, wet laps ~+7s.
                if weather_windows is not None:
                    condition = _get_condition_for_lap(race_lap, weather_windows)
                    lap_time += _CONDITION_OFFSETS.get(condition, 0.0)

                total_time += lap_time
                race_lap += 1

        # Pit stop penalties (one per stop)
        num_stops = len(compounds) - 1
        total_time += num_stops * pit_stop_loss_s

        # Escalating position loss — each successive stop costs more because
        # the driver increasingly encounters traffic on out-laps and loses
        # more positions to cars that have stopped fewer times.
        # Formula: sum(position_loss_s × stop_number) for stop_number 1..N
        #        = position_loss_s × N × (N + 1) / 2
        if position_loss_s > 0 and num_stops > 0:
            total_time += position_loss_s * num_stops * (num_stops + 1) / 2

        # Last-stint softness penalty: discourage ending on a softer compound
        # than the previous stint.  E.g., M→H→M should be penalized vs M→H→H.
        # Only applies to multi-stint strategies (2+ stints).
        if len(compounds) >= 2:
            last_softness = _COMPOUND_SOFTNESS.get(compounds[-1], 0)
            prev_softness = _COMPOUND_SOFTNESS.get(compounds[-2], 0)
            if last_softness > prev_softness:
                # Penalty scales with how much softer the last stint is
                tier_diff = last_softness - prev_softness
                total_time += _LAST_STINT_SOFT_PENALTY_S * tier_diff

        return total_time
