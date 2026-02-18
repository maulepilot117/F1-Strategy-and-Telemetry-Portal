import type { WeatherWindow } from "../types";
import styles from "./WeatherScenarioBuilder.module.css";

interface Props {
  raceLaps: number;
  weatherWindows: WeatherWindow[] | null;
  onWeatherWindowsChange: (windows: WeatherWindow[] | null) => void;
}

/* Human-readable labels and colors for each condition */
const CONDITION_OPTIONS: {
  value: WeatherWindow["condition"];
  label: string;
  color: string;
}[] = [
  { value: "dry", label: "Dry", color: "var(--text-primary)" },
  { value: "intermediate", label: "Light Rain (Inters)", color: "var(--compound-intermediate)" },
  { value: "wet", label: "Heavy Rain (Wets)", color: "var(--compound-wet)" },
];

/* Background colors for the weather preview bar */
const CONDITION_BAR_COLORS: Record<string, string> = {
  dry: "#333333",
  intermediate: "#0a3a0a",
  wet: "#0a2a4a",
};

/** Create default weather windows: single "dry" window covering the full race. */
function defaultWindows(raceLaps: number): WeatherWindow[] {
  return [{ start_lap: 1, end_lap: raceLaps, condition: "dry" }];
}

export default function WeatherScenarioBuilder({
  raceLaps,
  weatherWindows,
  onWeatherWindowsChange,
}: Props) {
  const isCustom = weatherWindows !== null;
  const windows = weatherWindows ?? defaultWindows(raceLaps);

  /** Toggle between "Dry Only" (null = use GET endpoint) and custom weather. */
  function handleToggle() {
    if (isCustom) {
      // Switch back to dry-only mode
      onWeatherWindowsChange(null);
    } else {
      // Enable custom weather with a default single dry window
      onWeatherWindowsChange(defaultWindows(raceLaps));
    }
  }

  /** Update the condition of a specific window. */
  function handleConditionChange(index: number, condition: WeatherWindow["condition"]) {
    const updated = windows.map((w, i) =>
      i === index ? { ...w, condition } : w,
    );
    onWeatherWindowsChange(updated);
  }

  /** Add a weather change by splitting the last window at its midpoint.
   *  This creates a new window for the second half with "intermediate" as default.
   */
  function handleAddChange() {
    if (windows.length >= 4) return; // Max 4 windows (3 transitions)

    const last = windows[windows.length - 1];
    const lastLaps = last.end_lap - last.start_lap + 1;

    // Need at least 10 laps in the last window to split (5 minimum per window)
    if (lastLaps < 10) return;

    const splitLap = last.start_lap + Math.floor(lastLaps / 2) - 1;

    const updated = [
      ...windows.slice(0, -1),
      { start_lap: last.start_lap, end_lap: splitLap, condition: last.condition },
      { start_lap: splitLap + 1, end_lap: last.end_lap, condition: "intermediate" as const },
    ];
    onWeatherWindowsChange(updated);
  }

  /** Remove a window by merging it with the previous one. */
  function handleRemove(index: number) {
    if (windows.length <= 1) return; // Can't remove the only window

    const updated = [...windows];
    const removed = updated.splice(index, 1)[0];

    // Extend the previous window (or next if removing the first) to cover the gap
    if (index > 0) {
      updated[index - 1] = {
        ...updated[index - 1],
        end_lap: removed.end_lap,
      };
    } else {
      updated[0] = {
        ...updated[0],
        start_lap: removed.start_lap,
      };
    }

    onWeatherWindowsChange(updated);
  }

  /** Update the transition lap between two adjacent windows.
   *  transitionIndex is the index of the boundary (window[i] ends, window[i+1] starts).
   */
  function handleTransitionChange(transitionIndex: number, newEndLap: number) {
    // Enforce minimum 5 laps per window
    const minEnd = windows[transitionIndex].start_lap + 4;
    const maxEnd = windows[transitionIndex + 1].end_lap - 5;
    const clamped = Math.max(minEnd, Math.min(maxEnd, newEndLap));

    const updated = windows.map((w, i) => {
      if (i === transitionIndex) {
        return { ...w, end_lap: clamped };
      }
      if (i === transitionIndex + 1) {
        return { ...w, start_lap: clamped + 1 };
      }
      return w;
    });
    onWeatherWindowsChange(updated);
  }

  return (
    <div className={styles.container}>
      {/* Toggle between dry-only and custom weather */}
      <div className={styles.toggleRow}>
        <label className={styles.toggleLabel}>Weather</label>
        <div className={styles.toggleButtons}>
          <button
            className={`${styles.toggleBtn} ${!isCustom ? styles.toggleActive : ""}`}
            onClick={() => !isCustom || handleToggle()}
          >
            Dry Only
          </button>
          <button
            className={`${styles.toggleBtn} ${isCustom ? styles.toggleActive : ""}`}
            onClick={() => isCustom || handleToggle()}
          >
            Custom Weather
          </button>
        </div>
      </div>

      {/* Custom weather editor — only visible when toggled on */}
      {isCustom && (
        <>
          {/* Weather preview bar: colored segments showing conditions across the race */}
          <div className={styles.previewBar}>
            {windows.map((win, i) => {
              const widthPct = ((win.end_lap - win.start_lap + 1) / raceLaps) * 100;
              return (
                <div
                  key={i}
                  className={styles.previewSegment}
                  style={{
                    width: `${widthPct}%`,
                    backgroundColor: CONDITION_BAR_COLORS[win.condition],
                  }}
                  title={`L${win.start_lap}-${win.end_lap}: ${win.condition}`}
                >
                  <span className={styles.previewLabel}>
                    L{win.start_lap}–{win.end_lap}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Window rows: condition dropdown + lap range */}
          <div className={styles.windowList}>
            {windows.map((win, i) => (
              <div key={i} className={styles.windowRow}>
                {/* Condition dropdown */}
                <select
                  value={win.condition}
                  onChange={(e) =>
                    handleConditionChange(i, e.target.value as WeatherWindow["condition"])
                  }
                  className={styles.conditionSelect}
                >
                  {CONDITION_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>

                {/* Lap range display */}
                <span className={styles.lapRange}>
                  L{win.start_lap}–{win.end_lap}{" "}
                  <span className={styles.lapCount}>
                    ({win.end_lap - win.start_lap + 1} laps)
                  </span>
                </span>

                {/* Transition lap input: adjusts the boundary between this and next window */}
                {i < windows.length - 1 && (
                  <div className={styles.transitionControl}>
                    <label className={styles.transitionLabel}>ends at</label>
                    <input
                      type="number"
                      min={win.start_lap + 4}
                      max={windows[i + 1].end_lap - 5}
                      value={win.end_lap}
                      onChange={(e) => handleTransitionChange(i, Number(e.target.value))}
                      className={styles.transitionInput}
                    />
                  </div>
                )}

                {/* Remove button (can't remove if only 1 window) */}
                {windows.length > 1 && (
                  <button
                    className={styles.removeBtn}
                    onClick={() => handleRemove(i)}
                    title="Remove this weather window"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Add weather change button */}
          {windows.length < 4 && (
            <button className={styles.addBtn} onClick={handleAddChange}>
              + Add Weather Change
            </button>
          )}
        </>
      )}
    </div>
  );
}
