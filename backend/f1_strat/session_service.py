"""
F1 session data service.

Wraps the FastF1 library to load practice, qualifying, and race session data.
All methods return plain Python dicts (not DataFrames) so the data is
JSON-serializable and ready for a future REST API.
"""

import logging
import statistics

import fastf1
import pandas as pd

from f1_strat.cache import setup_cache

logger = logging.getLogger(__name__)


def _td_to_seconds(td) -> float | None:
    """Convert a pandas Timedelta to total seconds, or None if missing."""
    if pd.isna(td):
        return None
    return round(td.total_seconds(), 3)


class SessionService:
    """Load and transform F1 session data into clean dictionaries."""

    def __init__(self):
        setup_cache()

        # In-memory cache for get_base_lap_time() results.  Keyed by
        # (year, grand_prix).  Base lap time loads FP1/FP2/FP3/Sprint
        # sessions to find the fastest clean lap — caching avoids
        # redundant session loads during live recalculation (20 drivers
        # × 4 sessions = 80 loads per pit event without caching).
        self._base_lap_cache: dict[tuple, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_session(
        self,
        year: int,
        grand_prix: str | int,
        session_type: str,
    ) -> dict:
        """Load a single session's data.

        Args:
            year: Season year (e.g. 2025).
            grand_prix: GP name (e.g. 'Abu Dhabi') or round number.
            session_type: One of 'FP1', 'FP2', 'FP3', 'Q', 'SQ', 'S', 'R'.

        Returns:
            Dict with keys: event_name, session_name, date, laps, weather,
            drivers, results.
        """
        logger.info("Loading %s %s %s ...", year, grand_prix, session_type)

        session = fastf1.get_session(year, grand_prix, session_type)
        session.load(laps=True, telemetry=False, weather=True, messages=False)

        return {
            "event_name": session.event["EventName"],
            "session_name": session.name,
            "date": str(session.date.date()),
            "laps": self._extract_laps(session),
            "weather": self._extract_weather(session),
            "drivers": self._extract_drivers(session),
            "results": self._extract_results(session),
        }

    def load_weekend(
        self,
        year: int,
        grand_prix: str | int,
    ) -> dict[str, dict]:
        """Load all practice and qualifying sessions for a race weekend.

        Tries FP1, FP2, FP3, and Qualifying.  On sprint weekends FP3 won't
        exist — it's silently skipped.

        Args:
            year: Season year.
            grand_prix: GP name or round number.

        Returns:
            Dict keyed by session name (e.g. 'Practice 1'), each value is the
            same structure returned by load_session().
        """
        sessions_to_try = ["FP1", "FP2", "FP3", "Q"]
        weekend = {}

        for session_type in sessions_to_try:
            try:
                data = self.load_session(year, grand_prix, session_type)
                weekend[data["session_name"]] = data
            except Exception as exc:
                # Sprint weekends don't have FP3 — this is expected
                logger.warning(
                    "Could not load %s for %s %s: %s",
                    session_type,
                    year,
                    grand_prix,
                    exc,
                )

        return weekend

    def get_weather_summary(self, year: int, grand_prix: str | int) -> dict:
        """Get a weather summary across all practice sessions.

        Loads FP1, FP2, and FP3 and aggregates weather data to give an
        overview of conditions during practice.  Useful for detecting
        whether it rained (which affects tyre compound choice) and for
        showing conditions alongside degradation analysis.

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.

        Returns:
            Dict with per-session breakdowns and overall aggregates.
            Includes a top-level 'had_rain' flag for quick checks.
        """
        all_weather_records = []
        sessions = {}

        for session_type in ("FP1", "FP2", "FP3"):
            try:
                session = fastf1.get_session(year, grand_prix, session_type)
                session.load(
                    laps=False, telemetry=False, weather=True, messages=False
                )
                weather_records = self._extract_weather(session)
                if weather_records:
                    sessions[session.name] = self._summarize_weather(
                        weather_records
                    )
                    all_weather_records.extend(weather_records)
            except Exception as exc:
                # Sprint weekends don't have FP3 — that's fine, skip it
                logger.warning("Could not load %s: %s", session_type, exc)

        # Build overall summary across all sessions
        overall = self._summarize_weather(all_weather_records)

        return {
            "had_rain": overall["had_rain"] if all_weather_records else False,
            "sessions": sessions,
            "overall": overall,
        }

    def _summarize_weather(self, weather_records: list[dict]) -> dict:
        """Aggregate a list of weather sample dicts into a summary.

        Computes min/max for temperatures and humidity, average wind
        speed, and a rain flag.  Reused for both per-session and
        cross-session aggregation.
        """
        if not weather_records:
            return {
                "had_rain": False,
                "air_temp_range_c": {"min": None, "max": None},
                "track_temp_range_c": {"min": None, "max": None},
                "humidity_range_pct": {"min": None, "max": None},
                "avg_wind_speed_ms": None,
                "sample_count": 0,
            }

        air_temps = [
            w["air_temp_c"] for w in weather_records if w["air_temp_c"] is not None
        ]
        track_temps = [
            w["track_temp_c"] for w in weather_records if w["track_temp_c"] is not None
        ]
        humidities = [
            w["humidity_pct"] for w in weather_records if w["humidity_pct"] is not None
        ]
        wind_speeds = [
            w["wind_speed_ms"] for w in weather_records if w["wind_speed_ms"] is not None
        ]
        had_rain = any(w.get("rainfall") is True for w in weather_records)

        return {
            "had_rain": had_rain,
            "air_temp_range_c": {
                "min": round(min(air_temps), 1) if air_temps else None,
                "max": round(max(air_temps), 1) if air_temps else None,
            },
            "track_temp_range_c": {
                "min": round(min(track_temps), 1) if track_temps else None,
                "max": round(max(track_temps), 1) if track_temps else None,
            },
            "humidity_range_pct": {
                "min": round(min(humidities), 1) if humidities else None,
                "max": round(max(humidities), 1) if humidities else None,
            },
            "avg_wind_speed_ms": (
                round(sum(wind_speeds) / len(wind_speeds), 1)
                if wind_speeds
                else None
            ),
            "sample_count": len(weather_records),
        }

    def get_race_info(self, year: int, grand_prix: str | int) -> dict | None:
        """Get race distance and pit stop time loss from actual race data.

        Loads the Race session to extract:
        - total_laps: the official race distance (e.g., 66 for Spain, 78 for Monaco)
        - avg_pit_stop_loss_s: median pit stop time loss computed from real pit stops

        Pit stop loss is calculated by comparing pit-in/out lap pairs against
        each driver's median clean lap time.  This captures the full cost of
        pitting: pit lane speed limit, stationary time, and cold-tyre out-lap.

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.

        Returns:
            Dict with 'total_laps' and 'avg_pit_stop_loss_s', or None if the
            race session isn't available (e.g., future races).
        """
        try:
            logger.info("Loading race session for %s %s ...", year, grand_prix)
            session = fastf1.get_session(year, grand_prix, "R")
            session.load(laps=True, telemetry=False, weather=False, messages=False)

            total_laps = session.total_laps

            # --- Compute pit stop loss from real pit stop data ---
            laps = session.laps
            if laps is None or laps.empty:
                return {"total_laps": int(total_laps), "avg_pit_stop_loss_s": None}

            pit_losses = []

            for driver, driver_laps in laps.groupby("Driver"):
                driver_laps = driver_laps.sort_values("LapNumber").reset_index(drop=True)

                # Get this driver's median clean lap time as a baseline.
                # "Clean" = has a valid time, is accurate, and isn't a pit lap.
                clean = driver_laps.dropna(subset=["LapTime"])
                clean = clean[clean["IsAccurate"] == True]  # noqa: E712
                clean = clean[clean["PitInTime"].isna() & clean["PitOutTime"].isna()]

                if clean.empty:
                    continue

                median_clean_s = clean["LapTime"].dt.total_seconds().median()

                # Find pit-in laps (where the driver entered the pits)
                pit_in_laps = driver_laps[driver_laps["PitInTime"].notna()]

                for _, pit_lap in pit_in_laps.iterrows():
                    pit_lap_num = pit_lap["LapNumber"]

                    # The next lap is the pit-out lap (leaving the pits)
                    out_lap_rows = driver_laps[
                        driver_laps["LapNumber"] == pit_lap_num + 1
                    ]
                    if out_lap_rows.empty:
                        continue

                    out_lap = out_lap_rows.iloc[0]

                    # Both laps need valid times to compute the loss
                    if pd.isna(pit_lap["LapTime"]) or pd.isna(out_lap["LapTime"]):
                        continue

                    in_time_s = pit_lap["LapTime"].total_seconds()
                    out_time_s = out_lap["LapTime"].total_seconds()

                    # Pit loss = (in_lap + out_lap) - (2 × normal lap)
                    # This captures the full cost: slow pit entry, stationary
                    # time, pit lane speed limit, and cold-tyre out-lap.
                    pit_loss = (in_time_s + out_time_s) - (2 * median_clean_s)

                    # Filter to 15-40s range — anything outside is likely a
                    # safety car period, red flag, or data error
                    if 15.0 <= pit_loss <= 40.0:
                        pit_losses.append(pit_loss)

            avg_pit_loss = None
            if pit_losses:
                # Median is more robust than mean — outlier safety car stops
                # won't skew the result
                avg_pit_loss = round(statistics.median(pit_losses), 1)

            return {
                "total_laps": int(total_laps),
                "avg_pit_stop_loss_s": avg_pit_loss,
            }

        except Exception as exc:
            # Race data might not be available (future races, cancelled sessions)
            logger.warning(
                "Could not load race info for %s %s: %s", year, grand_prix, exc
            )
            return None

    def get_base_lap_time(self, year: int, grand_prix: str | int) -> float:
        """Get the fastest clean practice lap time in seconds.

        Loads FP1, FP2, and FP3 and returns the single fastest accurate,
        non-deleted lap across all sessions.  This is used as the "ideal"
        baseline lap time for strategy simulation — the time a car would
        do on fresh tyres with no degradation.

        Results are cached per (year, grand_prix) — practice data is
        immutable for a given weekend, so the fastest lap never changes.

        Args:
            year: Season year.
            grand_prix: GP name or round number.

        Returns:
            Fastest lap time in seconds.

        Raises:
            ValueError: If no valid laps found in any practice session.
        """
        cache_key = (year, grand_prix)
        if cache_key in self._base_lap_cache:
            logger.debug("Base lap time cache hit for %s", cache_key)
            return self._base_lap_cache[cache_key]

        fastest = None

        # Include Sprint ("S") because drivers push harder in racing than
        # practice — sprint laps are often the fastest clean laps of the
        # weekend and give a more realistic base pace estimate.
        for session_type in ("FP1", "FP2", "FP3", "S"):
            try:
                session = fastf1.get_session(year, grand_prix, session_type)
                session.load(laps=True, telemetry=False, weather=False, messages=False)

                laps = session.laps
                if laps is None or laps.empty:
                    continue

                # Filter to clean, accurate laps only
                clean = laps.dropna(subset=["LapTime"])
                clean = clean[clean["IsAccurate"] == True]  # noqa: E712
                if "Deleted" in clean.columns:
                    clean = clean[clean["Deleted"] != True]  # noqa: E712

                if clean.empty:
                    continue

                # pick_fastest() returns None if no laps — check it
                best = clean.pick_fastest()
                if best is not None:
                    time_s = best["LapTime"].total_seconds()
                    if fastest is None or time_s < fastest:
                        fastest = time_s

            except Exception as exc:
                # No sprint on conventional weekends — expected
                logger.debug("Could not load %s: %s", session_type, exc)

        if fastest is None:
            raise ValueError(
                f"No valid practice laps found for {year} {grand_prix}"
            )

        result = round(fastest, 3)
        self._base_lap_cache[cache_key] = result
        logger.info("Base lap time cached for %s: %.3fs", cache_key, result)
        return result

    def get_q2_compounds(self, year: int, grand_prix: str | int) -> dict:
        """Extract Q2 fastest lap compound for top-10 qualifiers.

        The FIA Q2 tyre rule requires top-10 qualifiers to start the race
        on the same compound they used for their fastest Q2 lap.  This
        dramatically affects their strategy — a SOFT start forces an early
        first pit stop due to high degradation.

        Implementation:
        1. Load qualifying session and results
        2. Identify top-10 qualifiers (those with a Q3 time)
        3. For each top-10 driver, find their fastest Q2 lap → read Compound
        4. Return the mapping plus the most common Q2 compound

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.

        Returns:
            Dict with event_name, q2_compounds (driver→compound mapping),
            and top_10_starting_compound (most common Q2 compound).
        """
        logger.info("Loading qualifying for Q2 compounds: %s %s", year, grand_prix)

        session = fastf1.get_session(year, grand_prix, "Q")
        session.load(laps=True, telemetry=False, weather=False, messages=False)

        event_name = session.event["EventName"]
        results = session.results
        laps = session.laps

        if results is None or results.empty or laps is None or laps.empty:
            return {
                "event_name": event_name,
                "year": year,
                "q2_compounds": {},
                "top_10_starting_compound": None,
            }

        # Identify top-10 qualifiers: those who set a Q3 time.
        # In qualifying, Q3 participants are the top 10.
        top_10_drivers = []
        for _, row in results.iterrows():
            q3_time = row.get("Q3")
            if q3_time is not None and pd.notna(q3_time):
                top_10_drivers.append(row["Abbreviation"])

        q2_compounds = {}
        for driver in top_10_drivers:
            compound = self._get_driver_q2_compound(driver, results, laps)
            if compound:
                q2_compounds[driver] = compound

        # Find the most common Q2 compound — this is what most top-10
        # drivers will start on, and is the typical starting_compound
        # for strategy predictions
        most_common = None
        if q2_compounds:
            from collections import Counter
            counts = Counter(q2_compounds.values())
            most_common = counts.most_common(1)[0][0]

        return {
            "event_name": event_name,
            "year": year,
            "q2_compounds": q2_compounds,
            "top_10_starting_compound": most_common,
        }

    def _get_driver_q2_compound(
        self,
        driver: str,
        results: pd.DataFrame,
        laps: pd.DataFrame,
    ) -> str | None:
        """Find a driver's tyre compound for their fastest Q2 lap.

        First tries to match the Q2 result time exactly against laps.
        Falls back to finding the driver's fastest lap that could be
        a Q2 lap (i.e., not their Q3 time) if exact match fails.
        """
        driver_row = results[results["Abbreviation"] == driver]
        if driver_row.empty:
            return None

        driver_row = driver_row.iloc[0]
        q2_time = driver_row.get("Q2")
        if q2_time is None or pd.isna(q2_time):
            return None

        driver_laps = laps[laps["Driver"] == driver].copy()
        if driver_laps.empty:
            return None

        # Only consider laps with valid times and compounds
        driver_laps = driver_laps.dropna(subset=["LapTime", "Compound"])
        if driver_laps.empty:
            return None

        # Method 1: Find the lap whose LapTime matches the Q2 result time.
        # We compare with a small tolerance (~1ms) because of float precision.
        q2_seconds = q2_time.total_seconds()
        driver_laps["LapTime_s"] = driver_laps["LapTime"].dt.total_seconds()
        exact_match = driver_laps[
            abs(driver_laps["LapTime_s"] - q2_seconds) < 0.002
        ]
        if not exact_match.empty:
            return exact_match.iloc[0]["Compound"]

        # Method 2: Fallback — find the driver's second-fastest lap.
        # Their fastest lap is usually Q3, so Q2 is their second fastest.
        # Sort by time and take the second entry if it exists.
        sorted_laps = driver_laps.sort_values("LapTime_s")
        if len(sorted_laps) >= 2:
            return sorted_laps.iloc[1]["Compound"]
        elif len(sorted_laps) == 1:
            return sorted_laps.iloc[0]["Compound"]

        return None

    # ------------------------------------------------------------------
    # Data extraction helpers
    # ------------------------------------------------------------------

    def _extract_laps(self, session) -> list[dict]:
        """Convert the session's Laps DataFrame to a list of dicts."""
        laps = session.laps
        if laps is None or laps.empty:
            return []

        records = []
        for _, lap in laps.iterrows():
            records.append(
                {
                    "driver": lap["Driver"],
                    "driver_number": lap["DriverNumber"],
                    "lap_number": int(lap["LapNumber"]) if pd.notna(lap["LapNumber"]) else None,
                    "lap_time_s": _td_to_seconds(lap["LapTime"]),
                    "sector_1_s": _td_to_seconds(lap["Sector1Time"]),
                    "sector_2_s": _td_to_seconds(lap["Sector2Time"]),
                    "sector_3_s": _td_to_seconds(lap["Sector3Time"]),
                    "compound": lap["Compound"] if pd.notna(lap["Compound"]) else None,
                    "tyre_life": int(lap["TyreLife"]) if pd.notna(lap["TyreLife"]) else None,
                    "stint": int(lap["Stint"]) if pd.notna(lap["Stint"]) else None,
                    "fresh_tyre": bool(lap["FreshTyre"]) if pd.notna(lap["FreshTyre"]) else None,
                    "is_accurate": bool(lap["IsAccurate"]) if pd.notna(lap["IsAccurate"]) else None,
                    "position": int(lap["Position"]) if pd.notna(lap["Position"]) else None,
                    "deleted": bool(lap["Deleted"]) if pd.notna(lap.get("Deleted")) else None,
                }
            )
        return records

    def _extract_weather(self, session) -> list[dict]:
        """Convert the session's weather DataFrame to a list of dicts."""
        weather = session.weather_data
        if weather is None or weather.empty:
            return []

        records = []
        for _, row in weather.iterrows():
            records.append(
                {
                    "time_s": _td_to_seconds(row["Time"]),
                    "air_temp_c": row.get("AirTemp"),
                    "track_temp_c": row.get("TrackTemp"),
                    "humidity_pct": row.get("Humidity"),
                    "pressure_mbar": row.get("Pressure"),
                    "rainfall": bool(row.get("Rainfall")) if pd.notna(row.get("Rainfall")) else None,
                    "wind_speed_ms": row.get("WindSpeed"),
                    "wind_direction_deg": row.get("WindDirection"),
                }
            )
        return records

    def _extract_drivers(self, session) -> list[dict]:
        """Extract driver information from session results."""
        results = session.results
        if results is None or results.empty:
            return []

        drivers = []
        for _, row in results.iterrows():
            drivers.append(
                {
                    "number": row["DriverNumber"],
                    "abbreviation": row["Abbreviation"],
                    "full_name": row["FullName"],
                    "team": row["TeamName"],
                    "team_color": row.get("TeamColor"),
                }
            )
        return drivers

    def _extract_results(self, session) -> list[dict]:
        """Extract session results (finishing order / qualifying times)."""
        results = session.results
        if results is None or results.empty:
            return []

        records = []
        for _, row in results.iterrows():
            entry = {
                "position": int(row["Position"]) if pd.notna(row["Position"]) else None,
                "driver": row["Abbreviation"],
                "driver_number": row["DriverNumber"],
                "team": row["TeamName"],
                "best_lap_time_s": _td_to_seconds(row.get("Time")),
            }
            # Add qualifying-specific times if present
            for q_col in ("Q1", "Q2", "Q3"):
                val = row.get(q_col)
                if val is not None and pd.notna(val):
                    entry[q_col.lower() + "_s"] = _td_to_seconds(val)

            records.append(entry)
        return records
