"""
CLI entry point for the validation tool.

Usage:
    # Full season validation (first run: hours; subsequent: minutes with cache)
    PYTHONPATH=backend python -m f1_strat.validation --years 2025

    # Multiple years
    PYTHONPATH=backend python -m f1_strat.validation --years 2024 2025

    # Resume an interrupted run
    PYTHONPATH=backend python -m f1_strat.validation --resume

    # Single race for testing
    PYTHONPATH=backend python -m f1_strat.validation --years 2025 --race Australia
"""
# This file is the entry point — when you run `python -m f1_strat.validation`,
# Python actually looks for __main__.py in the package.  We import and call
# main() from validation.py to keep all logic in one place.
#
# Note: the module name in the -m flag is `f1_strat.validation` but Python
# runs `f1_strat/__main__.py` for `python -m f1_strat`, so we use a separate
# dispatcher approach.  To match the plan's CLI (`python -m f1_strat.validation`),
# we also need a __main__.py at the right level.  Since Python's -m flag for
# submodules doesn't support __main__.py the same way, we handle the CLI
# in this file and also check sys.argv for a dispatch.

import argparse
import logging
import sys

from f1_strat.validation import ValidationService


def main():
    parser = argparse.ArgumentParser(
        description="Validate F1 strategy engine against real race data."
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=[2025],
        help="Years to validate (default: 2025). Example: --years 2024 2025",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted run (skip races already in output file).",
    )
    parser.add_argument(
        "--race",
        type=str,
        default=None,
        help="Validate a single race only (e.g., --race Spain). Useful for testing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    service = ValidationService()

    if args.race:
        # Single race mode — useful for quick testing
        year = args.years[0]
        print(f"\nValidating single race: {year} {args.race}\n")
        result = service.compare_single_race(year, args.race)

        if result.get("skipped"):
            print(f"  SKIPPED: {result['skip_reason']}")
        else:
            # Print detailed results
            print(f"  Event: {result['event_name']}")
            print(f"  Laps:  {result['total_laps']}")
            print(f"  Pit stop loss: {result['pit_stop_loss_s']}s")
            print(f"  Deg rates: {result['deg_rates']}")
            print(f"\n  Our #1: {result['our_best_strategy']['name']}")
            print(f"    Pits at laps: {result['our_best_strategy']['pit_laps']}")

            # Show leading-team comparisons
            print(f"\n  Leading team strategies:")
            for tc in result.get("team_comparisons", []):
                rank_str = f"#{tc['strategy_rank']}" if tc["strategy_rank"] else "N/A"
                print(
                    f"    {tc['team']:20s} {tc['driver']:3s}: "
                    f"{' -> '.join(tc['compound_sequence'])}"
                )
                print(
                    f"      pits={tc['pit_laps']}  "
                    f"stop={'Y' if tc['stop_count_match'] else 'N'}  "
                    f"seq={'Y' if tc['sequence_match'] else 'N'}  "
                    f"set={'Y' if tc['compound_set_match'] else 'N'}  "
                    f"rank={rank_str}"
                )

            print(f"\n  Modal match: {result['modal_match']}")
            if result["modal_strategy"]:
                ms = result["modal_strategy"]
                print(
                    f"  Modal strategy: {' -> '.join(ms['compound_sequence'])} "
                    f"({ms['count']}/{ms['total_drivers']} leading-team drivers)"
                )

            # Show first 5 driver comparisons (all classified drivers)
            print(f"\n  All driver comparisons (first 5 of {result['num_classified']}):")
            for dc in result["driver_comparisons"][:5]:
                rank_str = f"#{dc['strategy_rank_in_predictions']}" if dc["strategy_rank_in_predictions"] else "N/A"
                print(
                    f"    {dc['driver']:3s}: "
                    f"{' -> '.join(dc['actual_sequence']):30s}  "
                    f"stops={'Y' if dc['stop_count_match'] else 'N'}  "
                    f"seq={'Y' if dc['compound_sequence_match'] else 'N'}  "
                    f"rank={rank_str}"
                )
    else:
        # Full validation across all specified years
        print(f"\nRunning validation for years: {args.years}")
        print(f"Resume mode: {'ON' if args.resume else 'OFF'}\n")
        service.run_all(years=args.years, resume=args.resume)


if __name__ == "__main__":
    main()
