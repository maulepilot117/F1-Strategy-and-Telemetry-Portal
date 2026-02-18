"""
Validation service for the strategy engine.

Compares our predicted pit stop strategies against what teams actually did
in real races across 2022-2024.  This tells us how accurate our predictions
are and where the engine needs improvement.

The core idea: if our engine says "1-stop Medium->Hard is optimal", and most
top-10 finishers actually did that, our prediction is good.  If they all did
2-stop strategies instead, we know we're systematically wrong about something.

Metrics we track:
  - Stop count match rate: did we predict the right number of stops?
  - Compound sequence match: did we get the exact compound order right?
  - Pit window accuracy: were our predicted pit laps within ±5 of reality?
  - Winner strategy rank: where does the winner's actual strategy rank in
    our predicted list?
"""

import json
import logging
from collections import Counter
from pathlib import Path

import fastf1
import pandas as pd

from f1_strat.cache import setup_cache
from f1_strat.degradation import DegradationService
from f1_strat.session_service import SessionService
from f1_strat.strategy import StrategyEngine

logger = logging.getLogger(__name__)

# Where validation output goes — gitignored so we don't commit large JSON files
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "validation_results"

# Wet-weather compounds — if any classified driver used these, we skip the race
# because our engine only handles dry conditions right now
_WET_COMPOUNDS = {"INTERMEDIATE", "WET"}

# Top teams to compare against — these have the best strategists and
# their strategies most closely represent the theoretical optimum.
# Uses exact FastF1 TeamName values (check session.results["TeamName"]).
_LEADING_TEAMS = {"McLaren", "Mercedes", "Red Bull Racing"}


class ValidationService:
    """Compare strategy engine predictions against real race data."""

    def __init__(self):
        setup_cache()
        self._degradation_service = DegradationService()
        self._strategy_engine = StrategyEngine()
        self._session_service = SessionService()

    # ------------------------------------------------------------------
    # Extract actual strategies from a real race
    # ------------------------------------------------------------------

    def extract_actual_strategies(self, year: int, grand_prix) -> dict:
        """Load a race and extract what every classified driver actually did.

        "Classified" means they finished the race and got an official result.
        Retired/DNF/DSQ drivers are excluded because their strategies were cut
        short and don't represent what the team planned.

        Args:
            year: Season year (e.g. 2024).
            grand_prix: GP name ('Spain') or round number.

        Returns:
            Dict with:
              - event_name: official event name
              - total_laps: race distance (from session metadata)
              - skip_reason: if set, this race should be excluded from analysis
              - drivers: list of per-driver strategy dicts
        """
        logger.info("Extracting actual strategies for %s %s ...", year, grand_prix)

        session = fastf1.get_session(year, grand_prix, "R")
        session.load(laps=True, telemetry=False, weather=False, messages=False)

        event_name = session.event["EventName"]
        total_laps = int(session.total_laps)
        results = session.results
        laps = session.laps

        if results is None or results.empty or laps is None or laps.empty:
            return {
                "event_name": event_name,
                "year": year,
                "total_laps": total_laps,
                "skip_reason": "no_data",
                "drivers": [],
            }

        # --- Filter to classified finishers only ---
        # ClassifiedPosition is the official finishing position.  For retired
        # drivers it's typically NaN or a string like 'R' (retired).  We only
        # want drivers where it's a valid integer (they finished the race).
        classified_drivers = []
        for _, row in results.iterrows():
            pos = row.get("ClassifiedPosition")
            if pos is None or pd.isna(pos):
                continue
            # Some FastF1 versions return it as a string or float
            try:
                int(pos)
                classified_drivers.append(row["Abbreviation"])
            except (ValueError, TypeError):
                continue

        if not classified_drivers:
            return {
                "event_name": event_name,
                "year": year,
                "total_laps": total_laps,
                "skip_reason": "no_classified_finishers",
                "drivers": [],
            }

        # --- Check if leader actually completed all laps ---
        # If the leader's max lap < total_laps, the race was likely
        # red-flagged and shortened.  We still analyze these but note it.
        leader_abbrev = classified_drivers[0]
        leader_laps = laps[laps["Driver"] == leader_abbrev]
        leader_max_lap = int(leader_laps["LapNumber"].max()) if not leader_laps.empty else 0
        shortened = leader_max_lap < total_laps

        # --- Build a lookup from driver abbreviation to team name ---
        # FastF1's results DataFrame has "Abbreviation" and "TeamName" columns.
        # We need team info so we can filter to leading teams later.
        driver_team = {}
        for _, row in results.iterrows():
            abbrev = row.get("Abbreviation")
            team = row.get("TeamName")
            if abbrev and team:
                driver_team[abbrev] = team

        # --- Extract strategy for each classified driver ---
        driver_strategies = []
        has_wet_compound = False

        for driver in classified_drivers:
            driver_laps = laps[laps["Driver"] == driver].sort_values("LapNumber")

            if driver_laps.empty:
                continue

            strategy = self._extract_driver_strategy(driver_laps)

            if strategy is None:
                continue

            # Check for wet compounds
            if any(c in _WET_COMPOUNDS for c in strategy["compound_sequence"]):
                has_wet_compound = True

            driver_strategies.append({
                "driver": driver,
                "team": driver_team.get(driver, "Unknown"),
                **strategy,
            })

        # --- Determine if we should skip this race ---
        skip_reason = None
        if has_wet_compound:
            skip_reason = "wet_race"
        elif shortened:
            skip_reason = "red_flag_shortened"
        elif not driver_strategies:
            skip_reason = "no_valid_strategies"

        return {
            "event_name": event_name,
            "year": year,
            "total_laps": total_laps,
            "leader_completed_laps": leader_max_lap,
            "skip_reason": skip_reason,
            "drivers": driver_strategies,
        }

    def _extract_driver_strategy(self, driver_laps: pd.DataFrame) -> dict | None:
        """Extract a single driver's strategy from their race laps.

        Groups laps by stint, determines the compound for each stint,
        and identifies pit laps (where PitInTime is not NaT).

        Returns:
            Dict with compound_sequence, pit_laps, and stint details,
            or None if the data is unusable.
        """
        stints = []
        pit_laps = []

        # Group by Stint number — each stint is a continuous run on one tyre set
        for stint_num, stint_group in driver_laps.groupby("Stint"):
            stint_group = stint_group.sort_values("LapNumber")

            # Get the compound for this stint.  Use the most common value
            # (mode) to handle rare cases where data shows mixed compounds
            # within a single stint due to timing glitches.
            compounds = stint_group["Compound"].dropna()
            if compounds.empty:
                continue

            compound = compounds.mode().iloc[0]

            # Skip stints with UNKNOWN compound — data quality issue
            if compound == "UNKNOWN":
                continue

            first_lap = int(stint_group["LapNumber"].min())
            last_lap = int(stint_group["LapNumber"].max())
            num_laps = last_lap - first_lap + 1

            stints.append({
                "stint_number": int(stint_num),
                "compound": compound,
                "first_lap": first_lap,
                "last_lap": last_lap,
                "num_laps": num_laps,
            })

            # Find pit laps — where PitInTime is not NaT (driver pitted)
            pit_in = stint_group[stint_group["PitInTime"].notna()]
            for _, pit_row in pit_in.iterrows():
                pit_laps.append(int(pit_row["LapNumber"]))

        if not stints:
            return None

        # Build the compound sequence (e.g., ["MEDIUM", "HARD", "SOFT"])
        # Sort stints by stint number to get the correct order
        stints.sort(key=lambda s: s["stint_number"])
        compound_sequence = [s["compound"] for s in stints]
        num_stops = len(compound_sequence) - 1

        return {
            "compound_sequence": compound_sequence,
            "num_stops": num_stops,
            "pit_laps": sorted(pit_laps),
            "stints": stints,
        }

    # ------------------------------------------------------------------
    # Run our strategy prediction
    # ------------------------------------------------------------------

    def run_prediction(
        self,
        year: int,
        grand_prix,
        race_laps: int,
        pit_stop_loss_s: float = 22.0,
    ) -> dict:
        """Run the strategy engine for a given race.

        Uses the DegradationService to get deg rates from practice, then
        runs StrategyEngine.calculate() to get ranked strategies.

        We use the race's actual pit stop loss from session data if available,
        falling back to the provided default.  We also enable 3-stop support
        since real races sometimes use 3 stops (e.g., Japan 2024, Qatar 2024).

        Args:
            year: Season year.
            grand_prix: GP name or round number.
            race_laps: Total race laps.
            pit_stop_loss_s: Fallback pit stop loss if we can't compute it.

        Returns:
            The full strategy engine output dict.
        """
        # Try to get actual pit stop loss from race data
        race_info = self._session_service.get_race_info(year, grand_prix)
        actual_pit_loss = pit_stop_loss_s
        if race_info and race_info.get("avg_pit_stop_loss_s") is not None:
            actual_pit_loss = race_info["avg_pit_stop_loss_s"]

        # Use max_stops=3 so validation covers 3-stop races (Japan 2024,
        # Qatar 2024).  The escalating position_loss_s penalty (default 3.0)
        # naturally discourages over-stopping, so we no longer need the
        # artificial max_stops=2 cap to prevent the model from over-stopping.
        return self._strategy_engine.calculate(
            year=year,
            grand_prix=grand_prix,
            race_laps=race_laps,
            pit_stop_loss_s=actual_pit_loss,
            max_stops=3,
        )

    # ------------------------------------------------------------------
    # Compare a single race: prediction vs reality
    # ------------------------------------------------------------------

    def compare_single_race(self, year: int, grand_prix) -> dict:
        """Compare our prediction against actual race strategies.

        This is the heart of the validation: for each classified finisher,
        we check how well our prediction matches what they actually did.

        Uses tiered matching:
          1. Stop count: did we get the right number of stops?
          2. Compound sequence: did we match the exact compound order?
          3. Compound set: same compounds regardless of order?
          4. Pit window: predicted pit laps within ±5 of actual?

        Args:
            year: Season year.
            grand_prix: GP name or round number.

        Returns:
            Dict with per-driver comparisons and race-level summary.
        """
        # --- Extract what actually happened ---
        actual = self.extract_actual_strategies(year, grand_prix)

        if actual["skip_reason"]:
            return {
                "year": year,
                "event_name": actual["event_name"],
                "total_laps": actual["total_laps"],
                "skipped": True,
                "skip_reason": actual["skip_reason"],
            }

        total_laps = actual["total_laps"]

        # --- Run our prediction ---
        try:
            prediction = self.run_prediction(year, grand_prix, total_laps)
        except Exception as exc:
            logger.warning(
                "Prediction failed for %s %s: %s", year, grand_prix, exc
            )
            return {
                "year": year,
                "event_name": actual["event_name"],
                "total_laps": total_laps,
                "skipped": True,
                "skip_reason": f"prediction_failed: {exc}",
            }

        predicted_strategies = prediction.get("strategies", [])
        if not predicted_strategies:
            return {
                "year": year,
                "event_name": actual["event_name"],
                "total_laps": total_laps,
                "skipped": True,
                "skip_reason": "no_predicted_strategies",
            }

        # Our top-predicted strategy (unconstrained — no Q2 tyre override)
        our_best = predicted_strategies[0]

        # --- Compare against each driver (kept for detailed output) ---
        driver_comparisons = []
        for driver_data in actual["drivers"]:
            comparison = self._compare_driver(
                driver_data, predicted_strategies
            )
            driver_comparisons.append(comparison)

        # --- Leading-team comparisons ---
        # Instead of comparing against only the race winner, we compare
        # against the best finisher from each leading team (McLaren, Mercedes,
        # Red Bull).  This gives up to 3 comparison targets per race —
        # more data points and less noise from one driver's unique situation.
        team_comparisons = self._build_team_comparisons(
            actual["drivers"],
            predicted_strategies,
        )

        # --- Modal strategy among leading-team drivers ---
        # Use leading-team drivers (not top-10) so the modal reflects what
        # the best strategists chose, not what the whole field did.
        leading_team_drivers = [
            d for d in actual["drivers"]
            if d.get("team") in _LEADING_TEAMS
        ]
        modal = self._compute_modal_strategy(leading_team_drivers)

        # Does our #1 match the modal strategy?
        modal_match = False
        if modal:
            modal_match = (
                tuple(s["compound"] for s in our_best["stints"])
                == tuple(modal["compound_sequence"])
            )

        # --- Safety car detection ---
        # Flag races where any leading-team driver's strategy was likely
        # influenced by a safety car.  Consecutive pit stops within 3 laps
        # of each other are a strong SC signal, as are 4+ stops.
        likely_safety_car = False
        for tc in team_comparisons:
            pit_laps_sorted = sorted(tc["pit_laps"])
            for i in range(1, len(pit_laps_sorted)):
                if pit_laps_sorted[i] - pit_laps_sorted[i - 1] <= 3:
                    likely_safety_car = True
                    break
            if tc["num_stops"] >= 4:
                likely_safety_car = True
            if likely_safety_car:
                break

        result = {
            "year": year,
            "event_name": actual["event_name"],
            "total_laps": total_laps,
            "skipped": False,
            "likely_safety_car": likely_safety_car,
            "our_best_strategy": {
                "name": our_best["name"],
                "num_stops": our_best["num_stops"],
                "compound_sequence": [s["compound"] for s in our_best["stints"]],
                "pit_laps": [
                    s["end_lap"]
                    for s in our_best["stints"][:-1]  # pit at end of each stint except last
                ],
            },
            "pit_stop_loss_s": prediction.get("pit_stop_loss_s"),
            "deg_rates": prediction.get("deg_rates_used"),
            "team_comparisons": team_comparisons,
            "modal_strategy": modal,
            "modal_match": modal_match,
            "driver_comparisons": driver_comparisons,
            "num_classified": len(actual["drivers"]),
        }

        return result

    def _build_team_comparisons(
        self,
        drivers: list[dict],
        predicted_strategies: list[dict],
    ) -> list[dict]:
        """Build per-team comparisons for leading teams.

        For each team in _LEADING_TEAMS, finds the best finisher (drivers
        are already in classification order from extract_actual_strategies),
        then compares our #1 prediction against that driver's actual strategy.

        Returns:
            List of comparison dicts, one per team (up to 3).
        """
        our_best = predicted_strategies[0]

        # Group drivers by team, take the first from each (= best finisher)
        best_per_team = {}
        for d in drivers:
            team = d.get("team")
            if team in _LEADING_TEAMS and team not in best_per_team:
                best_per_team[team] = d

        if not best_per_team:
            return []

        comparisons = []
        for team in sorted(best_per_team):
            driver_data = best_per_team[team]

            # Compare our #1 against this team's best finisher
            our_seq = tuple(s["compound"] for s in our_best["stints"])
            actual_seq = tuple(driver_data["compound_sequence"])

            stop_match = our_best["num_stops"] == driver_data["num_stops"]
            seq_match = our_seq == actual_seq
            set_match = set(our_seq) == set(actual_seq)
            rank = self._find_strategy_rank(
                driver_data["compound_sequence"], predicted_strategies
            )

            # Pit window deltas
            our_pits = [s["end_lap"] for s in our_best["stints"][:-1]]
            pit_deltas = compute_pit_deltas(our_pits, driver_data["pit_laps"])

            comparisons.append({
                "team": team,
                "driver": driver_data["driver"],
                "compound_sequence": list(actual_seq),
                "num_stops": driver_data["num_stops"],
                "pit_laps": driver_data["pit_laps"],
                "stop_count_match": stop_match,
                "sequence_match": seq_match,
                "compound_set_match": set_match,
                "strategy_rank": rank,
                "pit_deltas": pit_deltas,
            })

        return comparisons

    def _compare_driver(
        self, driver_data: dict, predicted_strategies: list[dict],
    ) -> dict:
        """Compare one driver's actual strategy against our predictions.

        Uses tiered matching:
          - stop_count_match: same number of pit stops?
          - compound_sequence_match: exact same compound order?
          - compound_set_match: same compounds regardless of order?
          - pit_window_deltas: for each pit stop, how far off were we?

        Returns a comparison dict.
        """
        actual_seq = tuple(driver_data["compound_sequence"])
        actual_stops = driver_data["num_stops"]
        actual_pits = driver_data["pit_laps"]

        our_best = predicted_strategies[0]
        our_seq = tuple(s["compound"] for s in our_best["stints"])
        our_stops = our_best["num_stops"]
        # Our predicted pit laps are at the end of each stint (except the last)
        our_pits = [s["end_lap"] for s in our_best["stints"][:-1]]

        # Tiered matching
        stop_count_match = our_stops == actual_stops
        sequence_match = our_seq == actual_seq
        set_match = set(our_seq) == set(actual_seq)

        # Pit window comparison: for each stop, how far off are we?
        pit_deltas = compute_pit_deltas(our_pits, actual_pits)

        # Where does this driver's strategy rank in our predictions?
        rank = self._find_strategy_rank(
            driver_data["compound_sequence"], predicted_strategies,
        )

        return {
            "driver": driver_data["driver"],
            "actual_sequence": list(actual_seq),
            "actual_stops": actual_stops,
            "actual_pit_laps": actual_pits,
            "stop_count_match": stop_count_match,
            "compound_sequence_match": sequence_match,
            "compound_set_match": set_match,
            "pit_deltas": pit_deltas,
            "strategy_rank_in_predictions": rank,
        }

    def _find_strategy_rank(
        self, actual_sequence: list[str], predicted_strategies: list[dict],
    ) -> int | None:
        """Find where an actual compound sequence ranks in our predictions.

        Looks for an exact compound sequence match in our ranked list.
        Returns the 1-based rank, or None if the sequence isn't in our list
        (e.g., it's a 3-stop strategy and we only generate up to 2-stop).
        """
        actual_tuple = tuple(actual_sequence)
        for strat in predicted_strategies:
            strat_seq = tuple(s["compound"] for s in strat["stints"])
            if strat_seq == actual_tuple:
                return strat["rank"]
        return None

    def _compute_modal_strategy(self, drivers: list[dict]) -> dict | None:
        """Find the most common compound sequence among a set of drivers.

        The "modal strategy" is the one most drivers chose — it represents
        the field consensus.  If our engine agrees with the modal strategy,
        we're predicting what most teams' strategists decided.

        Returns dict with compound_sequence and count, or None if empty.
        """
        if not drivers:
            return None

        # Count each compound sequence
        seq_counts = Counter()
        for d in drivers:
            seq_counts[tuple(d["compound_sequence"])] += 1

        # Most common
        most_common_seq, count = seq_counts.most_common(1)[0]
        return {
            "compound_sequence": list(most_common_seq),
            "count": count,
            "total_drivers": len(drivers),
        }

    # ------------------------------------------------------------------
    # Run validation across all races in one or more seasons
    # ------------------------------------------------------------------

    def run_all(
        self,
        years: list[int],
        resume: bool = False,
    ) -> dict:
        """Run validation across all races in the given years.

        Progress is printed after each race, and results are saved
        incrementally to JSON so the run survives interruption.

        Args:
            years: List of years to validate (e.g., [2022, 2023, 2024]).
            resume: If True, skip races already in the output file.

        Returns:
            Dict with race_results list and aggregate summary.
        """
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        details_path = _OUTPUT_DIR / "race_details.json"

        # Load existing results if resuming
        existing_results = []
        completed_keys = set()
        if resume and details_path.exists():
            with open(details_path) as f:
                existing_results = json.load(f)
            # Build a set of (year, event_name) keys we've already done
            for r in existing_results:
                completed_keys.add((r["year"], r["event_name"]))
            logger.info(
                "Resuming: %d races already completed", len(completed_keys)
            )

        race_results = list(existing_results)

        for year in years:
            # Get the schedule for this year
            schedule = fastf1.get_event_schedule(year)

            for _, event in schedule.iterrows():
                if event["RoundNumber"] == 0:
                    continue  # Skip testing events

                event_name = event["EventName"]

                # Skip if already completed (resume mode)
                if (year, event_name) in completed_keys:
                    print(f"  [SKIP] {year} {event_name} (already done)")
                    continue

                print(f"  [{year}] Analyzing {event_name} ...", end=" ", flush=True)

                try:
                    result = self.compare_single_race(year, event_name)
                    race_results.append(result)

                    if result.get("skipped"):
                        print(f"SKIPPED ({result['skip_reason']})")
                    else:
                        # Print a quick summary line showing per-team results
                        tc = result.get("team_comparisons", [])
                        n_teams = len(tc)
                        stops_y = sum(1 for t in tc if t["stop_count_match"])
                        seq_y = sum(1 for t in tc if t["sequence_match"])
                        ranks = [t["strategy_rank"] for t in tc if t["strategy_rank"] is not None]
                        avg_rank = f"#{sum(ranks)/len(ranks):.0f}" if ranks else "N/A"
                        print(
                            f"{n_teams} teams  "
                            f"stop={stops_y}/{n_teams}  "
                            f"seq={seq_y}/{n_teams}  "
                            f"avg_rank={avg_rank}"
                        )

                except Exception as exc:
                    logger.error("Failed on %s %s: %s", year, event_name, exc)
                    race_results.append({
                        "year": year,
                        "event_name": event_name,
                        "skipped": True,
                        "skip_reason": f"error: {exc}",
                    })
                    print(f"ERROR: {exc}")

                # Save after every race so we don't lose progress
                with open(details_path, "w") as f:
                    json.dump(race_results, f, indent=2)

        # Compute and save aggregates
        aggregates = self.compute_aggregates(race_results)
        summary_path = _OUTPUT_DIR / "summary.json"
        with open(summary_path, "w") as f:
            json.dump(aggregates, f, indent=2)

        # Print the summary
        self._print_summary(aggregates)

        return {
            "race_results": race_results,
            "aggregates": aggregates,
        }

    # ------------------------------------------------------------------
    # Aggregate metrics across all races
    # ------------------------------------------------------------------

    def compute_aggregates(self, race_results: list[dict]) -> dict:
        """Compute summary metrics across all analyzed races.

        Aggregates across *team comparisons* (up to 3 per race) rather than
        a single winner, giving more data points and less noise.

        Key metrics:
          - stop_count_match_rate: % of team comparisons where stops matched
          - compound_sequence_match_rate: exact compound order match
          - compound_set_match_rate: same compounds regardless of order
          - pit_window: deltas across all team comparisons
          - strategy_rank: avg rank, in-top-3, in-top-5
          - per_team: breakdown by McLaren, Mercedes, Red Bull
          - modal_match_rate: how often our #1 matches leading-team consensus

        Args:
            race_results: List of per-race result dicts from run_all().

        Returns:
            Dict with all aggregate metrics.
        """
        # Filter to non-skipped races only.
        analyzed = [r for r in race_results if not r.get("skipped", True)]
        analyzed_no_sc = [r for r in analyzed if not r.get("likely_safety_car")]

        if not analyzed:
            return {"total_races": 0, "analyzed_races": 0}

        total_races = len(race_results)
        skipped_races = len(race_results) - len(analyzed)

        # Skip reason breakdown
        skip_reasons = Counter()
        for r in race_results:
            if r.get("skipped"):
                skip_reasons[r.get("skip_reason", "unknown")] += 1

        # --- Flatten all team comparisons across all races ---
        # Each element is one team comparison (team driver vs our prediction).
        all_tc = []
        all_tc_no_sc = []
        for r in analyzed:
            for tc in r.get("team_comparisons", []):
                all_tc.append(tc)
        for r in analyzed_no_sc:
            for tc in r.get("team_comparisons", []):
                all_tc_no_sc.append(tc)

        total_comparisons = len(all_tc)
        if total_comparisons == 0:
            return {
                "total_races": total_races,
                "analyzed_races": len(analyzed),
                "skipped_races": skipped_races,
                "skip_reasons": dict(skip_reasons),
                "total_team_comparisons": 0,
            }

        # --- Stop count match rate (across all team comparisons) ---
        stop_matches = sum(1 for tc in all_tc if tc["stop_count_match"])
        stop_match_rate = round(stop_matches / total_comparisons * 100, 1)

        # Stop count confusion: predicted X but actual Y
        # Use our_best_strategy from each race since that's what we compare
        stop_confusion = Counter()
        for r in analyzed:
            for tc in r.get("team_comparisons", []):
                predicted = r["our_best_strategy"]["num_stops"]
                actual = tc["num_stops"]
                stop_confusion[(predicted, actual)] += 1

        # --- Compound sequence match rate ---
        seq_matches = sum(1 for tc in all_tc if tc["sequence_match"])
        seq_match_rate = round(seq_matches / total_comparisons * 100, 1)

        # --- Compound set match rate (same compounds, any order) ---
        set_matches = sum(1 for tc in all_tc if tc["compound_set_match"])
        set_match_rate = round(set_matches / total_comparisons * 100, 1)

        # --- Pit window accuracy ---
        all_pit_deltas = []
        for tc in all_tc:
            all_pit_deltas.extend(tc.get("pit_deltas", []))

        pit_within_3 = sum(1 for d in all_pit_deltas if abs(d) <= 3)
        pit_within_5 = sum(1 for d in all_pit_deltas if abs(d) <= 5)
        pit_accuracy_3 = round(pit_within_3 / len(all_pit_deltas) * 100, 1) if all_pit_deltas else 0
        pit_accuracy_5 = round(pit_within_5 / len(all_pit_deltas) * 100, 1) if all_pit_deltas else 0

        # Pit window bias: average signed delta (negative = we pit too early)
        pit_bias = round(sum(all_pit_deltas) / len(all_pit_deltas), 1) if all_pit_deltas else 0

        # --- Strategy rank distribution ---
        rank_values = [tc["strategy_rank"] for tc in all_tc if tc["strategy_rank"] is not None]
        not_found = sum(1 for tc in all_tc if tc["strategy_rank"] is None)
        in_top_3 = sum(1 for v in rank_values if v <= 3)
        in_top_5 = sum(1 for v in rank_values if v <= 5)
        avg_rank = round(sum(rank_values) / len(rank_values), 1) if rank_values else None

        # --- Modal strategy match rate ---
        modal_matches = sum(1 for r in analyzed if r.get("modal_match"))
        modal_match_rate = round(modal_matches / len(analyzed) * 100, 1)

        # --- Per-year breakdown ---
        by_year = {}
        for r in analyzed:
            yr = r["year"]
            if yr not in by_year:
                by_year[yr] = {
                    "races": 0, "comparisons": 0,
                    "stop_matches": 0, "seq_matches": 0, "set_matches": 0,
                }
            by_year[yr]["races"] += 1
            for tc in r.get("team_comparisons", []):
                by_year[yr]["comparisons"] += 1
                if tc["stop_count_match"]:
                    by_year[yr]["stop_matches"] += 1
                if tc["sequence_match"]:
                    by_year[yr]["seq_matches"] += 1
                if tc["compound_set_match"]:
                    by_year[yr]["set_matches"] += 1

        for yr_data in by_year.values():
            n = yr_data["comparisons"]
            yr_data["stop_match_rate"] = round(yr_data["stop_matches"] / n * 100, 1) if n else 0
            yr_data["seq_match_rate"] = round(yr_data["seq_matches"] / n * 100, 1) if n else 0
            yr_data["set_match_rate"] = round(yr_data["set_matches"] / n * 100, 1) if n else 0

        # --- Compound over/under-recommendation ---
        # Track how often we recommend each compound vs how often leading teams use it.
        # Both sides are counted per team comparison so the totals are comparable:
        # for each comparison, we add our predicted stints AND that team's actual stints.
        compound_predicted = Counter()
        compound_actual = Counter()
        for r in analyzed:
            for tc in r.get("team_comparisons", []):
                # Count our predicted compounds once per comparison (not per race)
                for c in r["our_best_strategy"]["compound_sequence"]:
                    compound_predicted[c] += 1
                # Count that team's actual compounds
                for c in tc["compound_sequence"]:
                    compound_actual[c] += 1

        # --- Per-team breakdown ---
        # Show how well we match each team individually
        per_team = {}
        for tc in all_tc:
            team = tc["team"]
            if team not in per_team:
                per_team[team] = {
                    "comparisons": 0, "stop_matches": 0,
                    "seq_matches": 0, "set_matches": 0,
                    "ranks": [],
                }
            per_team[team]["comparisons"] += 1
            if tc["stop_count_match"]:
                per_team[team]["stop_matches"] += 1
            if tc["sequence_match"]:
                per_team[team]["seq_matches"] += 1
            if tc["compound_set_match"]:
                per_team[team]["set_matches"] += 1
            if tc["strategy_rank"] is not None:
                per_team[team]["ranks"].append(tc["strategy_rank"])

        for team_data in per_team.values():
            n = team_data["comparisons"]
            team_data["stop_match_rate"] = round(team_data["stop_matches"] / n * 100, 1) if n else 0
            team_data["seq_match_rate"] = round(team_data["seq_matches"] / n * 100, 1) if n else 0
            team_data["set_match_rate"] = round(team_data["set_matches"] / n * 100, 1) if n else 0
            ranks = team_data.pop("ranks")
            team_data["avg_rank"] = round(sum(ranks) / len(ranks), 1) if ranks else None

        # --- Safety car exclusion metrics ---
        sc_count = len(analyzed) - len(analyzed_no_sc)
        if all_tc_no_sc:
            sc_stop_matches = sum(1 for tc in all_tc_no_sc if tc["stop_count_match"])
            sc_seq_matches = sum(1 for tc in all_tc_no_sc if tc["sequence_match"])
            sc_stop_rate = round(sc_stop_matches / len(all_tc_no_sc) * 100, 1)
            sc_seq_rate = round(sc_seq_matches / len(all_tc_no_sc) * 100, 1)
        else:
            sc_stop_rate = 0
            sc_seq_rate = 0

        return {
            "total_races": total_races,
            "analyzed_races": len(analyzed),
            "skipped_races": skipped_races,
            "skip_reasons": dict(skip_reasons),
            "total_team_comparisons": total_comparisons,
            "stop_count_match_rate_pct": stop_match_rate,
            "stop_count_confusion": {
                f"predicted_{p}_actual_{a}": count
                for (p, a), count in stop_confusion.most_common()
            },
            "compound_sequence_match_rate_pct": seq_match_rate,
            "compound_set_match_rate_pct": set_match_rate,
            "pit_window": {
                "total_pit_stops_compared": len(all_pit_deltas),
                "within_3_laps_pct": pit_accuracy_3,
                "within_5_laps_pct": pit_accuracy_5,
                "bias_laps": pit_bias,
            },
            "strategy_rank": {
                "in_top_3": in_top_3,
                "in_top_5": in_top_5,
                "not_found": not_found,
                "average_rank": avg_rank,
                "total_with_rank": len(rank_values),
            },
            "modal_match_rate_pct": modal_match_rate,
            "per_team": per_team,
            "safety_car_excluded": {
                "likely_sc_races": sc_count,
                "clean_races": len(analyzed_no_sc),
                "clean_comparisons": len(all_tc_no_sc),
                "stop_match_rate_pct": sc_stop_rate,
                "seq_match_rate_pct": sc_seq_rate,
            },
            "by_year": by_year,
            "compound_balance": {
                "predicted": dict(compound_predicted),
                "actual_leading_teams": dict(compound_actual),
            },
        }

    def _print_summary(self, agg: dict) -> None:
        """Print a human-readable summary of validation results."""
        print("\n" + "=" * 70)
        print("  VALIDATION SUMMARY (vs leading teams)")
        print("=" * 70)
        print(f"  Total races: {agg['total_races']}")
        print(f"  Analyzed:    {agg['analyzed_races']}")
        print(f"  Skipped:     {agg['skipped_races']}")
        if agg["skip_reasons"]:
            for reason, count in agg["skip_reasons"].items():
                print(f"    - {reason}: {count}")

        if agg["analyzed_races"] == 0:
            print("  No races analyzed!")
            print("=" * 70)
            return

        n_tc = agg.get("total_team_comparisons", 0)
        print(f"  Team comparisons: {n_tc} (across {agg['analyzed_races']} races)")

        print(f"\n  Stop count match:      {agg['stop_count_match_rate_pct']}%")
        print(f"  Compound seq match:    {agg['compound_sequence_match_rate_pct']}%")
        print(f"  Compound set match:    {agg['compound_set_match_rate_pct']}%")
        print(f"  Modal strategy match:  {agg['modal_match_rate_pct']}%")

        pw = agg["pit_window"]
        print(f"\n  Pit window (vs leading teams):")
        print(f"    Within ±3 laps: {pw['within_3_laps_pct']}%")
        print(f"    Within ±5 laps: {pw['within_5_laps_pct']}%")
        print(f"    Bias:           {pw['bias_laps']:+.1f} laps "
              f"({'too early' if pw['bias_laps'] < 0 else 'too late' if pw['bias_laps'] > 0 else 'neutral'})")

        sr = agg["strategy_rank"]
        print(f"\n  Leading-team strategies in our predictions:")
        print(f"    In top 3: {sr['in_top_3']}/{sr['total_with_rank']}")
        print(f"    In top 5: {sr['in_top_5']}/{sr['total_with_rank']}")
        if sr["average_rank"]:
            print(f"    Avg rank: {sr['average_rank']}")
        print(f"    Not found (e.g. 3-stop): {sr['not_found']}")

        # Per-team breakdown
        per_team = agg.get("per_team", {})
        if per_team:
            print(f"\n  Per-team breakdown:")
            for team in sorted(per_team):
                td = per_team[team]
                rank_str = f"avg_rank={td['avg_rank']}" if td["avg_rank"] else "avg_rank=N/A"
                print(
                    f"    {team:20s}  n={td['comparisons']:2d}  "
                    f"stop={td['stop_match_rate']}%  "
                    f"seq={td['seq_match_rate']}%  "
                    f"set={td['set_match_rate']}%  "
                    f"{rank_str}"
                )

        # Stop count confusion matrix
        print(f"\n  Stop count breakdown:")
        for key, count in agg["stop_count_confusion"].items():
            print(f"    {key}: {count}")

        # Per-year
        print(f"\n  By year:")
        for yr, data in sorted(agg["by_year"].items()):
            print(
                f"    {yr}: {data['races']} races ({data['comparisons']} comparisons), "
                f"stop={data['stop_match_rate']}%, "
                f"seq={data['seq_match_rate']}%, "
                f"set={data['set_match_rate']}%"
            )

        # Safety car exclusion
        sc = agg.get("safety_car_excluded", {})
        if sc.get("likely_sc_races"):
            print(
                f"\n  Excluding {sc['likely_sc_races']} likely safety-car races "
                f"({sc['clean_races']} clean, {sc['clean_comparisons']} comparisons):"
            )
            print(f"    Stop match (clean): {sc['stop_match_rate_pct']}%")
            print(f"    Seq match (clean):  {sc['seq_match_rate_pct']}%")

        # Compound balance
        print(f"\n  Compound usage (predicted vs leading teams):")
        actual_key = "actual_leading_teams"
        all_compounds = set(agg["compound_balance"]["predicted"]) | set(agg["compound_balance"][actual_key])
        for c in sorted(all_compounds):
            pred = agg["compound_balance"]["predicted"].get(c, 0)
            act = agg["compound_balance"][actual_key].get(c, 0)
            diff = pred - act
            print(f"    {c:8s}  predicted={pred:3d}  actual={act:3d}  diff={diff:+d}")

        print("\n" + "=" * 70)


# ------------------------------------------------------------------
# Module-level helpers (testable without a ValidationService instance)
# ------------------------------------------------------------------

def compute_pit_deltas(predicted_pits: list[int], actual_pits: list[int]) -> list[int]:
    """Compute the delta between predicted and actual pit laps.

    Pairs pit stops positionally (first predicted with first actual, etc.).
    Only compares up to the minimum number of stops — if one side has more
    stops, the extras are ignored (they represent a stop count mismatch,
    which is tracked separately).

    Positive delta = we predicted pitting later than reality.
    Negative delta = we predicted pitting earlier than reality.

    Args:
        predicted_pits: Our predicted pit lap numbers.
        actual_pits: The real pit lap numbers from the race.

    Returns:
        List of signed deltas (one per compared stop).
    """
    pairs = min(len(predicted_pits), len(actual_pits))
    return [predicted_pits[i] - actual_pits[i] for i in range(pairs)]


# ------------------------------------------------------------------
# CLI entry point: python -m f1_strat.validation
# ------------------------------------------------------------------

if __name__ == "__main__":
    from f1_strat.__main__ import main
    main()
