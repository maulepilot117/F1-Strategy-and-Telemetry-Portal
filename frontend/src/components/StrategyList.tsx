import type { StrategyResponse, WeatherWindow } from "../types";
import StrategyTimeline from "./StrategyTimeline";
import styles from "./StrategyList.module.css";

interface Props {
  data: StrategyResponse;
}

/** Format seconds into MM:SS.S (e.g., 5765.3 → "96:05.3") */
function formatTime(totalSeconds: number): string {
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}

/** Format a weather scenario as a short human-readable summary.
 *  e.g., "Dry (L1-19) → Rain (L20-40) → Dry (L41-66)"
 */
function formatWeatherSummary(windows: WeatherWindow[]): string {
  const labels: Record<string, string> = {
    dry: "Dry",
    intermediate: "Rain (Inters)",
    wet: "Rain (Wets)",
  };

  return windows
    .map((w) => `${labels[w.condition] ?? w.condition} (L${w.start_lap}–${w.end_lap})`)
    .join(" → ");
}

/* Show the top 10 strategies — the API can return 30+ but most aren't interesting */
const MAX_DISPLAY = 10;

export default function StrategyList({ data }: Props) {
  const strategies = data.strategies.slice(0, MAX_DISPLAY);

  if (strategies.length === 0) {
    return (
      <div className={styles.container}>
        <p style={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
          No strategies found. Check that degradation data is available.
        </p>
      </div>
    );
  }

  /* Build a short summary of what regulations are in effect */
  const regs = data.regulations;
  const regParts: string[] = [];
  regParts.push(`min ${regs.min_compounds} compounds`);
  regParts.push(`min ${regs.min_stops} stop${regs.min_stops !== 1 ? "s" : ""}`);
  if (regs.max_stint_laps) {
    regParts.push(`max ${regs.max_stint_laps} laps/stint`);
  }

  return (
    <div className={styles.container}>
      <span className={styles.title}>Race Strategies</span>

      {/* Show weather scenario summary when using mixed conditions */}
      {data.weather_windows && data.weather_windows.length > 0 && (
        <span className={styles.weatherSummary}>
          Weather: {formatWeatherSummary(data.weather_windows)}
        </span>
      )}

      {/* Show which FIA tyre regulations are being enforced */}
      <span className={styles.regulations}>
        FIA regulations: {regParts.join(", ")}
      </span>

      {strategies.map((strat) => {
        const isBest = strat.rank === 1;

        return (
          <div
            key={strat.name}
            className={`${styles.card} ${isBest ? styles.cardBest : ""}`}
          >
            {/* Header row: rank badge, strategy name, total time, gap */}
            <div className={styles.cardHeader}>
              <span
                className={`${styles.rank} ${isBest ? styles.rankBest : ""}`}
              >
                {strat.rank}
              </span>
              <span className={styles.name}>{strat.name}</span>
              <span className={styles.time}>{formatTime(strat.total_time_s)}</span>
              {strat.gap_to_best_s > 0 && (
                <span className={styles.gap}>+{strat.gap_to_best_s}s</span>
              )}
            </div>

            {/* Colored stint timeline bar — pass weather windows for overlay */}
            <StrategyTimeline
              stints={strat.stints}
              totalLaps={data.race_laps}
              weatherWindows={data.weather_windows}
            />

            {/* Text detail: which compound, which laps, and condition if mixed */}
            <div className={styles.stintDetails}>
              {strat.stints.map((s, i) => (
                <span key={i}>
                  {i > 0 && " → "}
                  {s.compound} (L{s.start_lap}–{s.end_lap}, {s.laps} laps)
                  {s.condition && s.condition !== "dry" && (
                    <span className={styles.conditionTag}>
                      {s.condition === "intermediate" ? " rain" : " wet"}
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
