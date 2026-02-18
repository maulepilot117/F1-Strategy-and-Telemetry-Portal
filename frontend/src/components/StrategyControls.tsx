import type { WeatherWindow } from "../types";
import WeatherScenarioBuilder from "./WeatherScenarioBuilder";
import styles from "./StrategyControls.module.css";

interface Props {
  raceLaps: number;
  onRaceLapsChange: (laps: number) => void;
  pitStopLoss: number;
  onPitStopLossChange: (loss: number) => void;
  onCalculate: () => void;
  loading: boolean;
  weatherWindows: WeatherWindow[] | null;
  onWeatherWindowsChange: (windows: WeatherWindow[] | null) => void;
  /** Race laps value from race data, if available (used for hint display) */
  raceDataLaps?: number;
  /** Pit stop loss from race data, if available (used for hint display) */
  raceDataPitLoss?: number;
}

export default function StrategyControls({
  raceLaps,
  onRaceLapsChange,
  pitStopLoss,
  onPitStopLossChange,
  onCalculate,
  loading,
  weatherWindows,
  onWeatherWindowsChange,
  raceDataLaps,
  raceDataPitLoss,
}: Props) {
  return (
    <div className={styles.container}>
      {/* Top row: race params + calculate button */}
      <div className={styles.row}>
        {/* Total race laps — varies by circuit (e.g., 66 for Spain, 78 for Monaco) */}
        <div className={styles.field}>
          <label htmlFor="raceLaps">Race Laps</label>
          <input
            id="raceLaps"
            type="number"
            min={10}
            max={100}
            value={raceLaps}
            onChange={(e) => onRaceLapsChange(Number(e.target.value))}
          />
          {raceDataLaps !== undefined && (
            <span className={styles.hint}>from race data</span>
          )}
        </div>

        {/* Pit stop time loss — how many seconds a pit stop costs vs staying out */}
        <div className={styles.field}>
          <label htmlFor="pitLoss">Pit Stop Loss (s)</label>
          <input
            id="pitLoss"
            type="number"
            min={15}
            max={35}
            step={0.5}
            value={pitStopLoss}
            onChange={(e) => onPitStopLossChange(Number(e.target.value))}
          />
          {raceDataPitLoss !== undefined && (
            <span className={styles.hint}>from race data</span>
          )}
        </div>

        <button
          className={styles.button}
          onClick={onCalculate}
          disabled={loading}
        >
          {loading ? "Calculating..." : "Calculate Strategy"}
        </button>
      </div>

      {/* Weather scenario builder — toggle between dry and custom weather */}
      <WeatherScenarioBuilder
        raceLaps={raceLaps}
        weatherWindows={weatherWindows}
        onWeatherWindowsChange={onWeatherWindowsChange}
      />
    </div>
  );
}
