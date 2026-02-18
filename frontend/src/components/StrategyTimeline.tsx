import type { Stint, WeatherWindow } from "../types";
import styles from "./StrategyTimeline.module.css";

interface Props {
  stints: Stint[];
  totalLaps: number;
  /** Optional weather windows — when present, renders a subtle background
   *  overlay showing weather conditions behind the stint bars. */
  weatherWindows?: WeatherWindow[];
}

/* Map compound names to background + text color pairs.
   Hard compound uses white background with dark text for readability.
   INTERMEDIATE uses F1 green, WET uses F1 blue. */
const COMPOUND_STYLES: Record<string, { bg: string; color: string }> = {
  SOFT: { bg: "#FF3333", color: "#fff" },
  MEDIUM: { bg: "#FFD700", color: "#111" },
  HARD: { bg: "#FFFFFF", color: "#111" },
  INTERMEDIATE: { bg: "#00CC00", color: "#111" },
  WET: { bg: "#0088FF", color: "#fff" },
};

/* Background overlay colors for weather conditions (subtle, behind stint bars) */
const WEATHER_BG: Record<string, string> = {
  dry: "transparent",
  intermediate: "rgba(0, 204, 0, 0.1)",
  wet: "rgba(0, 136, 255, 0.1)",
};

/* First letter of compound name for the stint label (e.g., "S28" = Soft, 28 laps) */
function compoundInitial(compound: string): string {
  return compound.charAt(0);
}

export default function StrategyTimeline({ stints, totalLaps, weatherWindows }: Props) {
  return (
    <div className={styles.timelineWrapper}>
      {/* Weather overlay: subtle colored background behind the stint bars */}
      {weatherWindows && weatherWindows.length > 0 && (
        <div className={styles.weatherOverlay}>
          {weatherWindows.map((win, i) => {
            const widthPct = ((win.end_lap - win.start_lap + 1) / totalLaps) * 100;
            return (
              <div
                key={i}
                className={styles.weatherSegment}
                style={{
                  width: `${widthPct}%`,
                  backgroundColor: WEATHER_BG[win.condition],
                }}
              />
            );
          })}
        </div>
      )}

      {/* Stint bars on top */}
      <div className={styles.timeline}>
        {stints.map((stint, i) => {
          const style = COMPOUND_STYLES[stint.compound] || {
            bg: "#666",
            color: "#fff",
          };
          // Width proportional to how many laps this stint covers
          const widthPct = (stint.laps / totalLaps) * 100;

          return (
            <div
              key={i}
              className={styles.stint}
              style={{
                width: `${widthPct}%`,
                backgroundColor: style.bg,
                color: style.color,
              }}
            >
              {compoundInitial(stint.compound)}
              {stint.laps}
            </div>
          );
        })}
      </div>
    </div>
  );
}
