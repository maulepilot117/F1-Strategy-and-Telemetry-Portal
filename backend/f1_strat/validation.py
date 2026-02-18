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

        # Our top-predicted strategy
        our_best = predicted_strategies[0]

        # --- Compare against each driver ---
        driver_comparisons = []
        for driver_data in actual["drivers"]:
            comparison = self._compare_driver(
                driver_data, predicted_strategies
            )
            driver_comparisons.append(comparison)

        # --- Compute race-level summary ---
        # Winner's strategy (first classified finisher)
        winner = actual["drivers"][0] if actual["drivers"] else None

        # Modal strategy among top-10 finishers: the most common compound
        # sequence, which represents the "consensus" strategy for this race
        top_10 = actual["drivers"][:10]
        modal = self._compute_modal_strategy(top_10)

        # Does our #1 prediction match the winner's stop count?
        winner_stop_match = False
        winner_sequence_match = False
        winner_rank = None
        if winner:
            winner_stop_match = our_best["num_stops"] == winner["num_stops"]
            winner_sequence_match = (
                tuple(s["compound"] for s in our_best["stints"])
                == tuple(winner["compound_sequence"])
            )
            # Where does the winner's actual strategy rank in our list?
            winner_rank = self._find_strategy_rank(
                winner["compound_sequence"], predicted_strategies
            )

        # Does our #1 match the modal strategy?
        modal_match = False
        if modal:
            modal_match = (
                tuple(s["compound"] for s in our_best["stints"])
                == tuple(modal["compound_sequence"])
            )

        # --- Q2-aware prediction for the winner ---
        # If the winner was a top-10 qualifier, run a second prediction
        # constrained to their Q2 starting compound.  Only use this as the
        # PRIMARY comparison when the winner's actual first compound matches
        # their Q2 compound (confirming they really did start on that tyre).
        # Otherwise keep it as supplementary data.
        q2_winner_rank = None
        q2_winner_compound = None
        q2_winner_strategy = None
        q2_stop_match = None
        q2_seq_match = None
        if winner:
            try:
                q2_data = self._session_service.get_q2_compounds(year, grand_prix)
                winner_q2 = q2_data["q2_compounds"].get(winner["driver"])
                if winner_q2:
                    q2_winner_compound = winner_q2
                    q2_prediction = self._strategy_engine.calculate(
                        year=year,
                        grand_prix=grand_prix,
                        race_laps=total_laps,
                        pit_stop_loss_s=prediction.get("pit_stop_loss_s", 22.0),
                        max_stops=3,
                        starting_compound=winner_q2,
                    )
                    q2_strategies = q2_prediction.get("strategies", [])
                    if q2_strategies:
                        q2_best = q2_strategies[0]
                        q2_winner_strategy = {
                            "name": q2_best["name"],
                            "compound_sequence": [
                                s["compound"] for s in q2_best["stints"]
                            ],
                            "pit_laps": [
                                s["end_lap"] for s in q2_best["stints"][:-1]
                            ],
                        }
                        q2_winner_rank = self._find_strategy_rank(
                            winner["compound_sequence"], q2_strategies,
                        )
                        q2_stop_match = q2_best["num_stops"] == winner["num_stops"]
                        q2_seq_match = (
                            tuple(s["compound"] for s in q2_best["stints"])
                            == tuple(winner["compound_sequence"])
                        )

                        # Only override the primary comparison when the winner
                        # actually started on their Q2 compound.  This confirms
                        # the Q2 rule was binding (they weren't penalized out of
                        # top 10, didn't change strategy, etc.).
                        winner_actual_first = winner["compound_sequence"][0]
                        if winner_actual_first == winner_q2:
                            our_best = q2_best
                            winner_stop_match = q2_stop_match
                            winner_sequence_match = q2_seq_match
                            winner_rank = q2_winner_rank

            except Exception as exc:
                logger.debug("Q2 data not available for %s %s: %s", year, grand_prix, exc)

        # --- Safety car detection ---
        # Flag races where the winner's strategy was likely influenced by a
        # safety car.  We detect this by checking for consecutive pit stops
        # within 3 laps of each other — planned strategies almost never have
        # stops that close together, but "free" safety car stops do.
        # Also flag extreme stop counts (4+) as likely SC/red-flag influenced.
        likely_safety_car = False
        if winner and winner["pit_laps"]:
            pit_laps_sorted = sorted(winner["pit_laps"])
            for i in range(1, len(pit_laps_sorted)):
                if pit_laps_sorted[i] - pit_laps_sorted[i - 1] <= 3:
                    likely_safety_car = True
                    break
            if winner["num_stops"] >= 4:
                likely_safety_car = True

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
            "winner": {
                "driver": winner["driver"] if winner else None,
                "compound_sequence": winner["compound_sequence"] if winner else None,
                "num_stops": winner["num_stops"] if winner else None,
                "pit_laps": winner["pit_laps"] if winner else None,
            },
            "winner_stop_count_match": winner_stop_match,
            "winner_sequence_match": winner_sequence_match,
            "winner_strategy_rank": winner_rank,
            "modal_strategy": modal,
            "modal_match": modal_match,
            "driver_comparisons": driver_comparisons,
            "num_classified": len(actual["drivers"]),
        }

        # Add Q2-aware comparison if available
        if q2_winner_compound:
            result["q2_analysis"] = {
                "winner_q2_compound": q2_winner_compound,
                "q2_best_strategy": q2_winner_strategy,
                "winner_rank_with_q2": q2_winner_rank,
            }

        return result

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
                        # Print a quick summary line
                        w_match = "Y" if result["winner_stop_count_match"] else "N"
                        w_rank = result["winner_strategy_rank"]
                        rank_str = f"#{w_rank}" if w_rank else "N/A"
                        print(
                            f"stop_match={w_match}  "
                            f"winner_rank={rank_str}  "
                            f"seq_match={'Y' if result['winner_sequence_match'] else 'N'}"
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

        Key metrics:
          - stop_count_match_rate: how often our #1 matches the winner's stops
          - compound_sequence_match_rate: exact compound order match
          - pit_window_accuracy: % of pit stops within ±5 laps and ±3 laps
          - pit_window_bias: signed average (negative = we pit too early)
          - winner_rank_distribution: histogram of where the winner's strategy
            ranks in our predictions
          - modal_match_rate: how often our #1 matches the field consensus

        Args:
            race_results: List of per-race result dicts from run_all().

        Returns:
            Dict with all aggregate metrics.
        """
        # Filter to non-skipped races only.
        # Also track which races had likely safety car influence — we still
        # include them in the main metrics but also report SC-excluded metrics
        # for a fairer view of engine accuracy.
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

        # --- Stop count match rate ---
        stop_matches = sum(1 for r in analyzed if r["winner_stop_count_match"])
        stop_match_rate = round(stop_matches / len(analyzed) * 100, 1)

        # Stop count confusion: predicted X but actual Y
        stop_confusion = Counter()
        for r in analyzed:
            predicted = r["our_best_strategy"]["num_stops"]
            actual = r["winner"]["num_stops"]
            stop_confusion[(predicted, actual)] += 1

        # --- Compound sequence match rate ---
        seq_matches = sum(1 for r in analyzed if r["winner_sequence_match"])
        seq_match_rate = round(seq_matches / len(analyzed) * 100, 1)

        # --- Compound set match rate (same compounds, any order) ---
        set_matches = 0
        for r in analyzed:
            our_set = set(r["our_best_strategy"]["compound_sequence"])
            winner_set = set(r["winner"]["compound_sequence"])
            if our_set == winner_set:
                set_matches += 1
        set_match_rate = round(set_matches / len(analyzed) * 100, 1)

        # --- Pit window accuracy ---
        all_pit_deltas = []
        for r in analyzed:
            our_pits = r["our_best_strategy"]["pit_laps"]
            winner_pits = r["winner"]["pit_laps"]
            deltas = compute_pit_deltas(our_pits, winner_pits)
            all_pit_deltas.extend(deltas)

        pit_within_3 = sum(1 for d in all_pit_deltas if abs(d) <= 3)
        pit_within_5 = sum(1 for d in all_pit_deltas if abs(d) <= 5)
        pit_accuracy_3 = round(pit_within_3 / len(all_pit_deltas) * 100, 1) if all_pit_deltas else 0
        pit_accuracy_5 = round(pit_within_5 / len(all_pit_deltas) * 100, 1) if all_pit_deltas else 0

        # Pit window bias: average signed delta (negative = we pit too early)
        pit_bias = round(sum(all_pit_deltas) / len(all_pit_deltas), 1) if all_pit_deltas else 0

        # --- Winner strategy rank distribution ---
        rank_values = [r["winner_strategy_rank"] for r in analyzed if r["winner_strategy_rank"] is not None]
        not_found = sum(1 for r in analyzed if r["winner_strategy_rank"] is None)
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
                by_year[yr] = {"races": 0, "stop_matches": 0, "seq_matches": 0}
            by_year[yr]["races"] += 1
            if r["winner_stop_count_match"]:
                by_year[yr]["stop_matches"] += 1
            if r["winner_sequence_match"]:
                by_year[yr]["seq_matches"] += 1

        for yr_data in by_year.values():
            n = yr_data["races"]
            yr_data["stop_match_rate"] = round(yr_data["stop_matches"] / n * 100, 1) if n else 0
            yr_data["seq_match_rate"] = round(yr_data["seq_matches"] / n * 100, 1) if n else 0

        # --- Compound over/under-recommendation ---
        # Track how often we recommend each compound vs how often it's actually used
        compound_predicted = Counter()
        compound_actual = Counter()
        for r in analyzed:
            for c in r["our_best_strategy"]["compound_sequence"]:
                compound_predicted[c] += 1
            for c in r["winner"]["compound_sequence"]:
                compound_actual[c] += 1

        # --- Safety car exclusion metrics ---
        # Repeat the key metrics excluding likely-SC-influenced races for a
        # fairer view of the engine's accuracy on "clean" races.
        sc_count = len(analyzed) - len(analyzed_no_sc)
        if analyzed_no_sc:
            sc_stop_matches = sum(1 for r in analyzed_no_sc if r["winner_stop_count_match"])
            sc_seq_matches = sum(1 for r in analyzed_no_sc if r["winner_sequence_match"])
            sc_stop_rate = round(sc_stop_matches / len(analyzed_no_sc) * 100, 1)
            sc_seq_rate = round(sc_seq_matches / len(analyzed_no_sc) * 100, 1)
        else:
            sc_stop_rate = 0
            sc_seq_rate = 0

        return {
            "total_races": total_races,
            "analyzed_races": len(analyzed),
            "skipped_races": skipped_races,
            "skip_reasons": dict(skip_reasons),
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
            "winner_strategy_rank": {
                "in_top_3": in_top_3,
                "in_top_5": in_top_5,
                "not_found": not_found,
                "average_rank": avg_rank,
                "total_with_rank": len(rank_values),
            },
            "modal_match_rate_pct": modal_match_rate,
            "safety_car_excluded": {
                "likely_sc_races": sc_count,
                "clean_races": len(analyzed_no_sc),
                "stop_match_rate_pct": sc_stop_rate,
                "seq_match_rate_pct": sc_seq_rate,
            },
            "by_year": by_year,
            "compound_balance": {
                "predicted": dict(compound_predicted),
                "actual_winner": dict(compound_actual),
            },
        }

    def _print_summary(self, agg: dict) -> None:
        """Print a human-readable summary of validation results."""
        print("\n" + "=" * 70)
        print("  VALIDATION SUMMARY")
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

        print(f"\n  Stop count match:      {agg['stop_count_match_rate_pct']}%")
        print(f"  Compound seq match:    {agg['compound_sequence_match_rate_pct']}%")
        print(f"  Compound set match:    {agg['compound_set_match_rate_pct']}%")
        print(f"  Modal strategy match:  {agg['modal_match_rate_pct']}%")

        pw = agg["pit_window"]
        print(f"\n  Pit window (vs winner):")
        print(f"    Within ±3 laps: {pw['within_3_laps_pct']}%")
        print(f"    Within ±5 laps: {pw['within_5_laps_pct']}%")
        print(f"    Bias:           {pw['bias_laps']:+.1f} laps "
              f"({'too early' if pw['bias_laps'] < 0 else 'too late' if pw['bias_laps'] > 0 else 'neutral'})")

        wr = agg["winner_strategy_rank"]
        print(f"\n  Winner's strategy in our predictions:")
        print(f"    In top 3: {wr['in_top_3']}/{wr['total_with_rank']}")
        print(f"    In top 5: {wr['in_top_5']}/{wr['total_with_rank']}")
        if wr["average_rank"]:
            print(f"    Avg rank: {wr['average_rank']}")
        print(f"    Not found (e.g. 3-stop): {wr['not_found']}")

        # Stop count confusion matrix
        print(f"\n  Stop count breakdown:")
        for key, count in agg["stop_count_confusion"].items():
            print(f"    {key}: {count}")

        # Per-year
        print(f"\n  By year:")
        for yr, data in sorted(agg["by_year"].items()):
            print(
                f"    {yr}: {data['races']} races, "
                f"stop_match={data['stop_match_rate']}%, "
                f"seq_match={data['seq_match_rate']}%"
            )

        # Safety car exclusion
        sc = agg.get("safety_car_excluded", {})
        if sc.get("likely_sc_races"):
            print(f"\n  Excluding {sc['likely_sc_races']} likely safety-car races ({sc['clean_races']} clean):")
            print(f"    Stop match (clean): {sc['stop_match_rate_pct']}%")
            print(f"    Seq match (clean):  {sc['seq_match_rate_pct']}%")

        # Compound balance
        print(f"\n  Compound usage (predicted vs actual winner):")
        all_compounds = set(agg["compound_balance"]["predicted"]) | set(agg["compound_balance"]["actual_winner"])
        for c in sorted(all_compounds):
            pred = agg["compound_balance"]["predicted"].get(c, 0)
            act = agg["compound_balance"]["actual_winner"].get(c, 0)
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
