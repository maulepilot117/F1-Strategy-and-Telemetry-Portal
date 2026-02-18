import type { ScheduleEvent } from "../types";
import styles from "./RaceSelector.module.css";

interface Props {
  year: number;
  onYearChange: (year: number) => void;
  schedule: ScheduleEvent[];
  grandPrix: string;
  onGrandPrixChange: (gp: string) => void;
  onAnalyze: () => void;
  loading: boolean;
}

/* Available seasons — add new years here as they become available */
const YEARS = [2025, 2024, 2023];

export default function RaceSelector({
  year,
  onYearChange,
  schedule,
  grandPrix,
  onGrandPrixChange,
  onAnalyze,
  loading,
}: Props) {
  return (
    <div className={styles.container}>
      {/* Year dropdown */}
      <div className={styles.field}>
        <label htmlFor="year">Season</label>
        <select
          id="year"
          value={year}
          onChange={(e) => onYearChange(Number(e.target.value))}
        >
          {YEARS.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>

      {/* Grand Prix dropdown — populated from the schedule API */}
      <div className={styles.field}>
        <label htmlFor="gp">Grand Prix</label>
        <select
          id="gp"
          value={grandPrix}
          onChange={(e) => onGrandPrixChange(e.target.value)}
          disabled={schedule.length === 0}
        >
          {schedule.length === 0 && <option value="">Loading...</option>}
          {schedule.map((ev) => (
            <option key={ev.round_number} value={ev.event_name}>
              {ev.event_name} ({ev.country})
            </option>
          ))}
        </select>
      </div>

      {/* Analyze button — fetches degradation data */}
      <button
        className={styles.button}
        onClick={onAnalyze}
        disabled={loading || !grandPrix}
      >
        {loading ? "Analyzing..." : "Analyze"}
      </button>
    </div>
  );
}
