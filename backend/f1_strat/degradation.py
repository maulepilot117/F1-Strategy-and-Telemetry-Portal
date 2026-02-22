"""
Tyre degradation analysis.

Calculates how much slower each tyre compound gets per lap, using real
practice session data.  This is the core analysis for building pit stop
strategies — if you know how fast each compound degrades, you can figure
out the optimal lap to pit.

The tricky part: as fuel burns off (~1.5 kg per lap), the car gets lighter
and naturally faster.  This masks tyre degradation in raw lap times.  We
correct for this by adding back the estimated fuel effect, so the curve
shows only the tyre-driven slowdown.

Community consensus is ~0.03 s/kg fuel effect, which at ~1.8 kg/lap
translates to ~0.055 s/lap.  We use this as the default correction.
"""

import logging

import fastf1
import numpy as np
import pandas as pd

from f1_strat.cache import setup_cache
from f1_strat.session_service import SessionService

logger = logging.getLogger(__name__)

# Only analyze these dry-weather compounds.
# INTERMEDIATE and WET behave completely differently and are rare in practice.
_DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}

# Wet-weather compounds — rarely used in practice but needed when it rains
_WET_COMPOUNDS = {"INTERMEDIATE", "WET"}

# Minimum degradation rate (s/lap) for any compound.  No tyre physically
# gets faster with age — negative rates are noise from track evolution or
# small samples.  0.02 s/lap is a realistic floor even for the hardest
# compound on the lowest-deg circuits.
_MIN_DEG_RATE = 0.02

# Sigma for Gaussian temperature weighting (°C).  Controls how sharply
# we down-weight practice stints that ran at different track temps vs
# the race.  With sigma=10:
#   5°C away  → weight 0.88  (highly relevant)
#  10°C away  → weight 0.61  (moderately relevant)
#  20°C away  → weight 0.14  (mostly irrelevant)
_TEMP_WEIGHT_SIGMA_C = 10.0

# Minimum data points required to attempt a quadratic (degree-2) fit.
# With fewer points, a quadratic overfits to noise — the linear fit is
# more robust.  10 points typically means 2-3 clean stints, which gives
# enough coverage across tyre ages to detect curvature reliably.
_MIN_POINTS_FOR_QUADRATIC = 10

# How many years of historical race data to use for stabilizing the
# quadratic degradation coefficient.  Each year provides ~40-60 clean
# stints per compound (20 drivers × 2-3 stints), vs ~10-20 from practice.
# Over 3 years that's 120-180 stints — enough to reliably detect curvature.
# Set to 0 to use practice data only (original behavior).
_DEFAULT_HISTORY_YEARS = 3

# Compound-specific maximum tyre age for the quadratic safety check.
# The safety check rejects the quadratic coefficient if the instantaneous

class DegradationService:
    """Analyze tyre degradation from practice session data."""

    def __init__(self):
        # Must enable cache before any FastF1 calls — see CLAUDE.md
        setup_cache()
        # SessionService is used to get weather summaries for practice sessions.
        # Its __init__ also calls setup_cache(), which is idempotent.
        self._session_service = SessionService()

    def _get_race_track_temp(
        self, year: int, grand_prix: str | int
    ) -> float | None:
        """Get the median track temperature during the actual race.

        Used to weight practice stints — stints run at similar temps to
        the race are more representative than those run in scorching
        afternoon practice.  Returns None for future races where no
        race data exists yet, in which case we fall back to equal weights.
        """
        try:
            session = fastf1.get_session(year, grand_prix, "R")
            # weather=True loads the ~1-per-minute weather samples;
            # laps=False keeps it lightweight since we only need temps
            session.load(
                laps=False, telemetry=False, weather=True, messages=False
            )
            weather = session.weather_data
            if weather is not None and not weather.empty:
                median_temp = float(weather["TrackTemp"].median())
                logger.info(
                    "Race track temp for %s %s: %.1f°C",
                    year, grand_prix, median_temp,
                )
                return median_temp
        except Exception as exc:
            logger.warning("Could not get race track temp: %s", exc)
        return None

    @staticmethod
    def _compute_temp_weight(
        stint_temp: float | None, race_temp: float | None
    ) -> float:
        """Gaussian weight based on temperature proximity to race conditions.

        Stints run at track temps close to the race get weight ~1.0;
        stints in very different conditions (e.g. +20°C hotter) get
        weight ~0.14.  Returns 1.0 when either temperature is unknown,
        so we fall back to equal weighting (identical to current behavior).
        """
        if stint_temp is None or race_temp is None:
            return 1.0
        if np.isnan(stint_temp) or np.isnan(race_temp):
            return 1.0
        diff = stint_temp - race_temp
        return float(np.exp(-(diff ** 2) / (2 * _TEMP_WEIGHT_SIGMA_C ** 2)))

    def analyze(
        self,
        year: int,
        grand_prix: str | int,
        fuel_correction_s: float = 0.055,
        history_years: int = _DEFAULT_HISTORY_YEARS,
    ) -> dict:
        """Calculate tyre degradation curves for a race weekend.

        Loads all practice sessions (FP1, FP2, FP3), pools the laps, and
        computes how each compound's lap time increases as the tyres age.

        Uses historical race data (previous years at the same circuit) to
        stabilize the quadratic degradation coefficient.  Shape (curvature)
        from history, rate from practice — see _load_historical_race_stints().

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.
            fuel_correction_s: Seconds per lap to add back for fuel burn-off.
                Community consensus is ~0.03 s/kg × ~1.8 kg/lap ≈ 0.055 s/lap.
                Cars get lighter each lap as fuel depletes, which hides tyre
                degradation in raw times.  We correct for this.
            history_years: How many years of historical race data to use for
                quadratic stabilization (default 3, 0 = practice only).

        Returns:
            Dict with a degradation curve per compound.  See module docstring
            or the test for the exact shape.
        """
        # -- Step 1: Load practice sessions and collect all laps -----------
        all_laps, event_name = self._load_practice_laps(year, grand_prix)

        # Instead of returning early when practice is empty, we let the
        # pipeline flow through so historical race data can fill in compound
        # curves.  This fixes 3 races that previously produced no strategies
        # due to insufficient practice data (e.g., sprint weekends with only
        # FP1 and poor weather, or cancelled sessions).
        if all_laps.empty:
            deltas = pd.DataFrame()
            baselines = []
        else:
            # -- Step 2: Filter out noisy laps -----------------------------
            clean_laps = self._filter_laps(all_laps)

            # -- Step 3: Compute fuel-corrected deltas per stint -----------
            deltas, baselines = self._compute_stint_deltas(
                clean_laps, fuel_correction_s
            )

        # -- Step 3b: Load historical race data for quadratic stabilization --
        # Historical races at the same circuit provide 120-180 stints (vs
        # ~10-20 from practice), giving reliable curvature detection.  The
        # absolute rate still comes from current-year practice only.
        historical_deltas = self._load_historical_race_stints(
            year, grand_prix,
            history_years=history_years,
            fuel_correction_s=fuel_correction_s,
        )

        # -- Step 4: Get race-day track temperature for weighting ----------
        # Practice stints run at similar temps to the race are more
        # representative.  Returns None for future races → equal weights.
        race_track_temp = self._get_race_track_temp(year, grand_prix)

        # -- Step 5: Average by compound + tyre age to get curves ----------
        compounds = self._build_curves(
            deltas,
            race_track_temp=race_track_temp,
            historical_deltas=historical_deltas,
        )

        # -- Step 6: Check for wet compound usage in raw practice data -----
        # If any driver ran INTERMEDIATE or WET tyres in practice, we can
        # extract real degradation data for those compounds.
        # Guard against empty practice data (all_laps may be empty when we
        # fall through to historical-only mode).
        wet_compounds_available = False
        if not all_laps.empty and "Compound" in all_laps.columns:
            practice_compounds = set(all_laps["Compound"].dropna().unique())
            wet_compounds_available = bool(
                practice_compounds & _WET_COMPOUNDS
            )

        # Ensure event_name is always a string for the response dict.
        # When practice sessions fail to load (rare), event_name may be None.
        event_name = event_name or str(grand_prix)

        # -- Step 7: Get weather summary for practice sessions -------------
        weather_summary = self._session_service.get_weather_summary(
            year, grand_prix
        )

        # -- Step 8: Get race info (total laps + pit stop loss) -------------
        # This loads the Race session to extract the official race distance
        # and compute pit stop loss from actual pit stop data.  Wrapped in
        # try/except so degradation analysis still works if race data isn't
        # available (future races, sprint weekends without a race loaded, etc.)
        race_info = None
        try:
            race_info = self._session_service.get_race_info(year, grand_prix)
        except Exception as exc:
            logger.warning("Could not get race info: %s", exc)

        # -- Step 7b: Compute compound base pace offsets --------------------
        # Fresh SOFT tyres are inherently faster than fresh HARD tyres.
        # This offset captures that gap from practice data, so the strategy
        # engine can properly account for each compound's starting pace.
        compound_offsets = self._compute_compound_offsets(baselines)

        result = {
            "event_name": event_name,
            "year": year,
            "fuel_correction_s_per_lap": fuel_correction_s,
            "compounds": compounds,
            "compound_offsets": compound_offsets,
            "weather_summary": weather_summary,
            "wet_compounds_available": wet_compounds_available,
        }

        # Include the race-day track temperature used for weighting.
        # None means no race data was available (future race) and equal
        # weights were used.  The frontend can display this to the user
        # so they understand why certain stints were weighted higher.
        if race_track_temp is not None:
            result["race_track_temp_c"] = round(race_track_temp, 1)

        # Include historical data metadata so the frontend can show which
        # years contributed to the quadratic stabilization.
        if not historical_deltas.empty:
            result["historical_years_used"] = sorted(
                int(y) for y in historical_deltas["source_year"].unique()
            )
            result["historical_data_points"] = len(historical_deltas)

        # Add race info fields if available — the frontend uses these to
        # auto-populate Race Laps and Pit Stop Loss inputs
        if race_info is not None:
            if race_info.get("total_laps") is not None:
                result["race_laps"] = race_info["total_laps"]
            if race_info.get("avg_pit_stop_loss_s") is not None:
                result["avg_pit_stop_loss_s"] = race_info["avg_pit_stop_loss_s"]

        return result

    def analyze_wet(
        self,
        year: int,
        grand_prix: str | int,
        fuel_correction_s: float = 0.055,
    ) -> dict:
        """Calculate degradation curves for wet-weather compounds.

        Same pipeline as analyze() but filters to INTERMEDIATE and WET
        compounds.  Most weekends this returns empty compounds because
        teams rarely run wet tyres in dry practice.  When data exists
        (rain during practice), it provides real deg rates that can
        override user-provided defaults.

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.
            fuel_correction_s: Same fuel correction as dry analysis.

        Returns:
            Same dict structure as analyze(), but only wet compound data.
        """
        all_laps, event_name = self._load_practice_laps(year, grand_prix)

        if all_laps.empty:
            return {
                "event_name": event_name or str(grand_prix),
                "year": year,
                "fuel_correction_s_per_lap": fuel_correction_s,
                "compounds": {},
            }

        # Filter to wet compounds only
        clean_laps = self._filter_laps(all_laps, compounds=_WET_COMPOUNDS)

        if clean_laps.empty:
            return {
                "event_name": event_name,
                "year": year,
                "fuel_correction_s_per_lap": fuel_correction_s,
                "compounds": {},
            }

        deltas, _baselines = self._compute_stint_deltas(
            clean_laps, fuel_correction_s
        )
        race_track_temp = self._get_race_track_temp(year, grand_prix)
        compounds = self._build_curves(
            deltas, race_track_temp=race_track_temp
        )

        result = {
            "event_name": event_name,
            "year": year,
            "fuel_correction_s_per_lap": fuel_correction_s,
            "compounds": compounds,
        }
        if race_track_temp is not None:
            result["race_track_temp_c"] = round(race_track_temp, 1)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_practice_laps(
        self, year: int, grand_prix: str | int
    ) -> tuple[pd.DataFrame, str | None]:
        """Load and concatenate laps from all practice sessions.

        We skip qualifying because drivers do short single-lap runs there,
        which don't show meaningful degradation.

        Returns:
            (combined_laps_dataframe, event_name)
        """
        frames = []
        event_name = None

        for session_type in ("FP1", "FP2", "FP3"):
            try:
                logger.info("Loading %s %s %s ...", year, grand_prix, session_type)
                session = fastf1.get_session(year, grand_prix, session_type)
                # weather=True so we can tag each lap with its track temp —
                # used later to weight stints by temperature proximity to race
                session.load(
                    laps=True, telemetry=False, weather=True, messages=False
                )
                if event_name is None:
                    event_name = session.event["EventName"]
                if session.laps is not None and not session.laps.empty:
                    # Tag each lap with the track temperature at that point
                    # in the session.  Must be done per-session because each
                    # session has its own time reference frame.
                    tagged = self._tag_laps_with_track_temp(session)
                    frames.append(tagged)
            except Exception as exc:
                # Sprint weekends don't have FP3 — that's fine, skip it
                logger.warning("Could not load %s: %s", session_type, exc)

        # Sprint race laps are valuable degradation data — drivers push hard
        # for ~25-30 laps on race compounds, producing clean long-run data.
        # This is especially important on sprint weekends where only FP1 is
        # available, giving us 1-2 extra compounds worth of deg data.
        # Sprint laps have the same DataFrame columns as practice laps
        # (LapTime, Compound, Stint, TyreLife, IsAccurate), so the existing
        # filtering pipeline handles them with no changes.
        try:
            session = fastf1.get_session(year, grand_prix, "S")
            session.load(
                laps=True, telemetry=False, weather=True, messages=False
            )
            if event_name is None:
                event_name = session.event["EventName"]
            if session.laps is not None and not session.laps.empty:
                tagged = self._tag_laps_with_track_temp(session)
                frames.append(tagged)
                logger.info(
                    "Loaded %d sprint laps for %s %s",
                    len(tagged), year, grand_prix,
                )
        except Exception:
            # No sprint on conventional weekends (18+ of 24) — expected
            logger.debug("No sprint session for %s %s", year, grand_prix)

        if not frames:
            return pd.DataFrame(), event_name

        return pd.concat(frames, ignore_index=True), event_name

    def _load_historical_race_stints(
        self,
        year: int,
        grand_prix: str | int,
        history_years: int = _DEFAULT_HISTORY_YEARS,
        fuel_correction_s: float = 0.055,
    ) -> pd.DataFrame:
        """Load race stints from previous years at the same circuit.

        Historical race data stabilizes the quadratic (curvature) coefficient
        in the degradation model.  Each race provides ~40-60 clean stints per
        compound (20 drivers × 2-3 stints), far more than the ~10-20 from
        practice.  Over 3 years that's 120-180 stints — enough to reliably
        detect how degradation curves flatten at high tyre ages.

        The key insight: curvature (quadratic coefficient) is a circuit-specific
        property driven by corner types and surface roughness, which is stable
        across seasons.  The absolute rate (linear coefficient) depends on the
        current year's compound formulations, so that still comes from practice.

        Args:
            year: Current season year.  We load year-1, year-2, ... year-N.
            grand_prix: GP name or round number.
            history_years: How many past years to load (default 3, 0 = skip).
            fuel_correction_s: Same fuel correction as the practice pipeline.

        Returns:
            DataFrame with columns: Compound, tyre_age, corrected_delta_s,
            stint_track_temp_c, source_year.  Empty DataFrame if no historical
            data could be loaded.
        """
        if history_years <= 0:
            return pd.DataFrame()

        frames = []

        for offset in range(1, history_years + 1):
            hist_year = year - offset
            try:
                logger.info(
                    "Loading historical race %s %s ...", hist_year, grand_prix
                )
                session = fastf1.get_session(hist_year, grand_prix, "R")
                # weather=True so we can temperature-weight historical stints
                session.load(
                    laps=True, telemetry=False, weather=True, messages=False
                )

                if session.laps is None or session.laps.empty:
                    logger.debug(
                        "No laps in %s %s race", hist_year, grand_prix
                    )
                    continue

                # Run through the same pipeline as practice data:
                # tag with track temp → filter noisy laps → compute stint deltas
                tagged = self._tag_laps_with_track_temp(session)
                clean = self._filter_laps(tagged)

                if clean.empty:
                    logger.debug(
                        "No clean laps after filtering %s %s race",
                        hist_year, grand_prix,
                    )
                    continue

                deltas, _baselines = self._compute_stint_deltas(
                    clean, fuel_correction_s
                )

                if not deltas.empty:
                    # Tag with source year for traceability in logs/debugging
                    deltas = deltas.copy()
                    deltas["source_year"] = hist_year
                    frames.append(deltas)
                    logger.info(
                        "Got %d historical stint data points from %s %s race",
                        len(deltas), hist_year, grand_prix,
                    )

            except Exception as exc:
                # Missing years are expected — new circuits, cancelled races,
                # or races not yet in the FastF1 database
                logger.debug(
                    "Could not load %s %s race: %s",
                    hist_year, grand_prix, exc,
                )

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    def _tag_laps_with_track_temp(
        self, session
    ) -> pd.DataFrame:
        """Merge track temperature from weather data onto each lap.

        Uses merge_asof to find the nearest weather sample for each lap's
        start time.  Weather is sampled ~once per minute, so the match is
        within ~30s of the actual lap start.

        When no weather data is available for a session, we still return
        the laps — TrackTemp will be NaN and the weighting will fall back
        to equal weights (1.0) for those stints.
        """
        laps = session.laps
        weather = session.weather_data

        if weather is not None and not weather.empty and "LapStartTime" in laps.columns:
            laps_sorted = laps.sort_values("LapStartTime").dropna(
                subset=["LapStartTime"]
            )
            weather_sorted = weather.sort_values("Time")

            # merge_asof matches each lap to the nearest weather sample
            # by timestamp.  direction="nearest" finds the closest match
            # regardless of whether it's before or after the lap start.
            merged = pd.merge_asof(
                laps_sorted,
                weather_sorted[["Time", "TrackTemp"]],
                left_on="LapStartTime",
                right_on="Time",
                direction="nearest",
            )
            # Drop the extra "Time" column from weather to avoid confusion
            if "Time" in merged.columns:
                merged = merged.drop(columns=["Time"])
            return merged

        # No weather data — return laps as-is (TrackTemp will be missing)
        return laps

    def _filter_laps(
        self, laps: pd.DataFrame, compounds: set[str] | None = None,
    ) -> pd.DataFrame:
        """Remove laps that would add noise to the degradation analysis.

        We filter out:
        - Laps with no recorded time (pit in/out laps often have no time)
        - Laps marked as inaccurate by FastF1 (timing sync issues)
        - Deleted laps (steward-deleted for track limits, etc.)
        - Laps not matching the target compound set
        - Outlier laps slower than 107% of the fastest lap (traffic, mistakes,
          cool-down laps).  107% is the same threshold F1 uses for qualifying.

        Args:
            laps: Raw laps DataFrame from FastF1.
            compounds: Which compounds to keep.  Defaults to _DRY_COMPOUNDS.
                Pass _WET_COMPOUNDS to analyze wet-weather tyres.
        """
        # Default to dry compounds if none specified
        compounds = compounds or _DRY_COMPOUNDS

        filtered = laps.copy()

        # Must have a valid lap time
        filtered = filtered.dropna(subset=["LapTime"])

        # Only accurate laps (FastF1 checks timing sync)
        filtered = filtered[filtered["IsAccurate"] == True]  # noqa: E712

        # Only keep green-flag laps.  TrackStatus is a concatenation of all
        # status codes during the lap: "1" = all green, "12" = yellow appeared,
        # "4" or "14" = safety car, "6" = VSC.  We only want pure green laps
        # ("1") so that SC/VSC slowdowns don't corrupt our degradation curves.
        # Our 107% outlier filter catches full SC laps (~50% slower) but misses
        # VSC laps (~10-15% slower) and SC transition laps.
        if "TrackStatus" in filtered.columns:
            filtered = filtered[filtered["TrackStatus"] == "1"]

        # Remove steward-deleted laps if the column exists and has data
        if "Deleted" in filtered.columns:
            filtered = filtered[filtered["Deleted"] != True]  # noqa: E712

        # Only the specified compounds
        filtered = filtered[filtered["Compound"].isin(compounds)]

        # Must have stint and tyre life info for grouping
        filtered = filtered.dropna(subset=["Stint", "TyreLife"])

        # Remove outliers: slower than 107% of fastest lap
        if not filtered.empty:
            # Convert lap times to seconds for comparison
            filtered = filtered.copy()
            filtered["LapTime_s"] = filtered["LapTime"].dt.total_seconds()
            fastest = filtered["LapTime_s"].min()
            cutoff = fastest * 1.07
            filtered = filtered[filtered["LapTime_s"] <= cutoff]

        return filtered

    def _compute_stint_deltas(
        self, laps: pd.DataFrame, fuel_correction_s: float
    ) -> tuple[pd.DataFrame, list[dict]]:
        """For each stint, compute the fuel-corrected time delta.

        A "stint" is a continuous run on one set of tyres.  We group by
        driver + stint number, then:
          1. Drop the first lap (always an out-lap with cold tyres)
          2. Find the "peak grip" lap — the fastest lap in the first half.
             This handles compound-specific warm-up: softs peak on lap 1-2,
             hards may take 4-5 laps to reach temperature.
          3. Only measure degradation from the peak onward
          4. Remove outlier laps within the stint (traffic, mistakes)
          5. fuel_corrected_delta = raw_delta + (fuel_correction * laps_since_peak)

        The fuel correction is ADDED because fuel burn-off makes the car
        faster, which hides degradation.  By adding it back, we isolate
        the tyre effect.

        Only stints with 4+ laps are included — shorter stints are typically
        push-lap qualifying simulations, not race-pace runs.

        Returns:
            Tuple of (deltas_df, baselines_list):
            - deltas_df: DataFrame with columns: Compound, tyre_age,
              corrected_delta_s, stint_track_temp_c (median track temp for
              the stint, NaN if no weather data).
            - baselines_list: List of dicts with Compound, baseline_s
              (peak grip lap time in seconds), and stint_track_temp_c for
              each valid stint.  Used to compute compound base pace offsets.
        """
        records = []
        baselines = []

        # Group by driver and stint — each group is one continuous tyre run
        for (driver, stint), group in laps.groupby(["Driver", "Stint"]):
            group = group.sort_values("LapNumber").reset_index(drop=True)

            # Need at least 5 laps to be a meaningful race-simulation run.
            # Shorter stints are often qualifying sims or installation laps
            # where the driver changes mode mid-stint, producing data that
            # looks like "negative degradation" and corrupts the analysis.
            if len(group) < 5:
                continue

            # Drop the first lap — it's always an out-lap with cold tyres.
            group = group.iloc[1:].reset_index(drop=True)

            # Find "peak grip" — the fastest lap in the first half of
            # the stint.  This naturally adapts to each compound:
            #   - Softs warm up in ~1 lap, so peak is near the start
            #   - Hards need 3-4 laps, so peak is deeper into the stint
            # We only look at the first half to avoid picking a random
            # fast lap from late in the stint (tow effect, track evolution).
            half = max(len(group) // 2, 1)
            first_half = group.iloc[:half]
            peak_idx = int(first_half["LapTime_s"].idxmin())

            # Only use laps from the peak onward — this is the degradation
            # phase.  Everything before the peak is warm-up.
            deg_laps = group.iloc[peak_idx:].reset_index(drop=True)

            # Remove outlier laps within this stint.  In practice, drivers
            # often hit traffic which inflates a single lap by 1-3+ seconds.
            # We remove laps > median + 1.0s to keep the curve clean.
            if len(deg_laps) >= 3:
                median_s = deg_laps["LapTime_s"].median()
                deg_laps = deg_laps[deg_laps["LapTime_s"] <= median_s + 1.0]

            # Need at least 3 laps after cleanup to fit a trend
            if len(deg_laps) < 3:
                continue

            compound = deg_laps["Compound"].iloc[0]
            baseline_s = deg_laps["LapTime_s"].iloc[0]

            # Compute the median track temperature for this stint.
            # Used later to weight this stint by how representative its
            # conditions are of the actual race.  NaN if no weather data
            # was available for this session.
            stint_temp = float("nan")
            if "TrackTemp" in deg_laps.columns:
                temp_vals = deg_laps["TrackTemp"].dropna()
                if not temp_vals.empty:
                    stint_temp = float(temp_vals.median())

            # Record the baseline (peak grip lap time) for this stint.
            # Used later to compute compound base pace offsets — fresh
            # SOFT tyres are inherently faster than fresh HARD tyres,
            # independent of how fast each compound degrades.
            baselines.append({
                "Compound": compound,
                "baseline_s": baseline_s,
                "stint_track_temp_c": stint_temp,
            })

            for i, (_, lap) in enumerate(deg_laps.iterrows()):
                raw_delta = lap["LapTime_s"] - baseline_s

                # Fuel correction: on lap i after peak, the car is lighter
                # by (i * fuel_per_lap), making it ~(i * 0.055)s faster.
                # We add this back to reveal the true tyre degradation.
                corrected_delta = raw_delta + (fuel_correction_s * i)

                records.append(
                    {
                        "Compound": compound,
                        # tyre_age starts at 1 (peak grip lap = age 1)
                        "tyre_age": i + 1,
                        "corrected_delta_s": round(corrected_delta, 3),
                        "stint_track_temp_c": stint_temp,
                    }
                )

        return pd.DataFrame(records), baselines

    @staticmethod
    def _compute_compound_offsets(baselines: list[dict]) -> dict[str, float]:
        """Compute per-compound base pace offsets from peak grip lap times.

        Fresh SOFT tyres are inherently faster than fresh HARD tyres,
        independent of degradation rate.  This method measures that gap
        using the median peak-grip lap time from practice stints.

        The offset is 0.0 for the fastest compound (usually SOFT) and
        positive for slower ones.  For example:
            {"SOFT": 0.0, "MEDIUM": 0.52, "HARD": 1.14}
        means that on fresh tyres, MEDIUM is 0.52s/lap slower than SOFT
        and HARD is 1.14s/lap slower.

        Uses median (not mean) for robustness — consistent with our
        median-over-mean pattern throughout the degradation pipeline.
        Only includes dry compounds (SOFT, MEDIUM, HARD).

        Args:
            baselines: List of dicts from _compute_stint_deltas(), each
                with Compound, baseline_s, and stint_track_temp_c.

        Returns:
            Dict mapping compound name to offset in seconds.
            Empty dict if no baselines are available.
        """
        if not baselines:
            return {}

        df = pd.DataFrame(baselines)

        # Only dry compounds — wet compounds are handled separately
        df = df[df["Compound"].isin(_DRY_COMPOUNDS)]
        if df.empty:
            return {}

        # Median baseline per compound — robust to outliers from
        # traffic, fuel loads, or different car modes in practice
        medians = df.groupby("Compound")["baseline_s"].median()

        # Offset = difference from the fastest compound (usually SOFT).
        # The fastest compound gets offset 0.0, slower compounds get
        # positive values showing how much extra time they add per lap.
        fastest = medians.min()
        offsets = {
            compound: round(float(median - fastest), 3)
            for compound, median in medians.items()
        }

        logger.info(
            "Compound base pace offsets: %s (vs %s at %.3fs)",
            offsets,
            medians.idxmin(),
            fastest,
        )

        return offsets

    def _build_curves(
        self,
        deltas: pd.DataFrame,
        race_track_temp: float | None = None,
        historical_deltas: pd.DataFrame | None = None,
    ) -> dict:
        """Average the deltas by compound + tyre age to build smooth curves.

        Also fits a linear regression to each compound's curve to get a
        single "seconds per lap" degradation rate — the headline number
        that strategists use (e.g., "the soft degrades at 0.08s/lap").

        The regression is temperature-weighted: practice stints run at
        track temps close to the actual race get higher weight.  When
        race_track_temp is None (future races), all weights are 1.0 and
        behavior is identical to the previous unweighted version.

        The quadratic coefficient uses combined practice + historical race
        data for a more stable fit.  Historical data is NOT filtered by
        practice valid_ages because race stints go to 20-30+ laps —
        exactly the high-age points where curvature is most visible.

        Args:
            deltas: DataFrame from _compute_stint_deltas() with columns
                Compound, tyre_age, corrected_delta_s, stint_track_temp_c.
            race_track_temp: Median race-day track temp in °C, or None.
            historical_deltas: DataFrame from _load_historical_race_stints()
                with the same columns plus source_year, or None/empty.

        Returns:
            Dict keyed by compound name, each with 'degradation_per_lap_s'
            and 'curve' (list of {tyre_age, avg_delta_s, sample_count}).
        """
        # Only process compounds that have practice data.  Compounds
        # without practice data are left to the strategy engine's
        # calibrated fallback rates (HARD=60%×MEDIUM, SOFT=160%×MEDIUM).
        # Historical race data is used to stabilize the quadratic
        # coefficient for compounds that DO have practice data, but
        # not as a standalone source of degradation rates — historical
        # races show lower deg due to tyre management and track evolution,
        # which would make HARD look artificially attractive.
        if deltas.empty:
            return {}

        compounds = {}

        for compound, group in deltas.groupby("Compound"):
            if compound not in _DRY_COMPOUNDS:
                continue

            # --- Display curve: unweighted median (unchanged) ---
            # Take the median across all stints/drivers at each tyre age.
            # Median is more robust than mean when sample sizes are small
            # (common for hard tyres in practice) — a single outlier stint
            # won't skew the result as much.
            curve_df = (
                group.groupby("tyre_age")
                .agg(
                    avg_delta_s=("corrected_delta_s", "median"),
                    sample_count=("corrected_delta_s", "count"),
                )
                .reset_index()
                .sort_values("tyre_age")
            )

            # Only keep tyre ages where we have enough data (2+ samples)
            # to avoid wild averages from a single stint.  We use 2 rather
            # than 3 because hard tyre long runs are less common in practice
            # — there may only be 2-3 stints across all sessions.
            curve_df = curve_df[curve_df["sample_count"] >= 2]

            if curve_df.empty:
                continue

            # --- Degradation slope: temperature-weighted regression ---
            # We fit on the raw per-stint data points (not aggregated medians)
            # so that each stint carries its own temperature weight.  Stints
            # run at track temps close to the race get weight ~1.0; stints
            # in very different conditions are down-weighted.
            # Only include tyre ages that survived the 2+ sample filter above
            # — this prevents single-stint noise at rare tyre ages from
            # distorting the slope.
            valid_ages = set(curve_df["tyre_age"].values)
            filtered_group = group[group["tyre_age"].isin(valid_ages)]
            x_all = filtered_group["tyre_age"].values.astype(float)
            y_all = filtered_group["corrected_delta_s"].values.astype(float)

            # Compute a temperature weight for each data point based on
            # its stint's track temperature vs the race-day temperature
            if "stint_track_temp_c" in filtered_group.columns:
                w_all = np.array([
                    self._compute_temp_weight(t, race_track_temp)
                    for t in filtered_group["stint_track_temp_c"]
                ])
            else:
                w_all = np.ones(len(x_all))

            if len(x_all) >= 2:
                slope, _ = np.polyfit(x_all, y_all, 1, w=w_all)
                deg_per_lap = round(float(slope), 4)
            else:
                deg_per_lap = 0.0

            # Floor: no tyre physically gets faster with age.  Negative rates
            # come from noisy practice data (e.g. track evolution within a
            # session masking degradation).  0.02 s/lap is a realistic minimum
            # even for the hardest compounds on low-deg circuits.
            deg_per_lap = max(deg_per_lap, _MIN_DEG_RATE)

            # --- Quadratic fit for the strategy engine ----------------------
            # Real tyres typically degrade in a slightly concave pattern:
            # degradation flattens over time rather than increasing linearly.
            # A degree-2 polynomial captures this: per-lap degradation at
            # tyre_age t is  linear*t + quadratic*t².
            #
            # When quadratic < 0 (concave), long stints are less penalized
            # than a pure linear model would predict — reducing the model's
            # tendency to over-recommend pit stops.
            #
            # KEY INSIGHT: "shape from history, rate from practice."
            # - The quadratic coefficient (curvature) is fitted on combined
            #   practice + historical race data.  Curvature is a circuit
            #   property (corner types, surface roughness) stable across years.
            # - The linear coefficient stays from practice-only degree-1 fit,
            #   reflecting current-season compound formulations.
            #
            # Historical data is NOT filtered by practice valid_ages because
            # race stints go to 20-30+ laps — those high-age points are
            # exactly where curvature is most visible.
            #
            # Safety check: if the instantaneous rate (linear + 2*quad*t)
            # would drop below _MIN_DEG_RATE at t=40, we reject the quadratic
            # and fall back to linear-only (quad=0).
            quad_coeff = 0.0
            linear_coeff = deg_per_lap  # default: same as degree-1 slope

            # Build historical data arrays for this compound
            hist_x = np.array([])
            hist_y = np.array([])
            hist_w = np.array([])
            if historical_deltas is not None and not historical_deltas.empty:
                hist_compound = historical_deltas[
                    historical_deltas["Compound"] == compound
                ]
                if not hist_compound.empty:
                    hist_x = hist_compound["tyre_age"].values.astype(float)
                    hist_y = hist_compound["corrected_delta_s"].values.astype(
                        float
                    )
                    # Temperature-weight historical stints too — years with
                    # similar track temps to this year's race are more relevant
                    if "stint_track_temp_c" in hist_compound.columns:
                        hist_w = np.array([
                            self._compute_temp_weight(t, race_track_temp)
                            for t in hist_compound["stint_track_temp_c"]
                        ])
                    else:
                        hist_w = np.ones(len(hist_x))

            # Combine practice + historical for the degree-2 fit
            combined_x = np.concatenate([x_all, hist_x])
            combined_y = np.concatenate([y_all, hist_y])
            combined_w = np.concatenate([w_all, hist_w])

            if len(combined_x) >= _MIN_POINTS_FOR_QUADRATIC:
                # np.polyfit returns [a, b, c] for a*x² + b*x + c
                a_raw, b_raw, _ = np.polyfit(
                    combined_x, combined_y, 2, w=combined_w
                )

                # Compound-specific safety check: reject if the instantaneous
                # degradation rate would drop below _MIN_DEG_RATE at the
                # compound's realistic max stint length.
                #
                # IMPORTANT: we use deg_per_lap (practice-only linear) here,
                # NOT b_raw (combined fit's linear).  The strategy engine
                # will use: total_deg(t) = linear*t + quadratic*t²
                # so instantaneous rate = linear + 2*quadratic*t.
                # Since linear = deg_per_lap, the check must use that.
                instantaneous_at_40 = deg_per_lap + 2 * a_raw * 40
                if instantaneous_at_40 >= _MIN_DEG_RATE:
                    quad_coeff = round(float(a_raw), 6)
                    # linear_coeff stays as deg_per_lap from practice-only
                    # degree-1 fit — current-season rate, not historical
                else:
                    logger.debug(
                        "%s: quadratic fit rejected (rate at t=40 would be "
                        "%.4f, below floor %.4f)",
                        compound, instantaneous_at_40, _MIN_DEG_RATE,
                    )

            # Convert to plain Python dicts for JSON serialization
            curve = [
                {
                    "tyre_age": int(row["tyre_age"]),
                    "avg_delta_s": round(float(row["avg_delta_s"]), 3),
                    "sample_count": int(row["sample_count"]),
                }
                for _, row in curve_df.iterrows()
            ]

            compounds[compound] = {
                "degradation_per_lap_s": deg_per_lap,
                # deg_coefficients is used by the strategy engine for
                # more accurate lap-by-lap simulation.  The "linear" term
                # is the per-lap rate and "quadratic" captures curvature.
                # When quadratic=0, this is identical to the old linear model.
                "deg_coefficients": {
                    "linear": linear_coeff,
                    "quadratic": quad_coeff,
                },
                "curve": curve,
            }

        return compounds
