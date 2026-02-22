import { useState, useEffect } from "react";
import type { ScheduleEvent, DegradationResponse, StrategyResponse, WeatherWindow } from "./types";
import { fetchSchedule, fetchDegradation, fetchStrategy, fetchWeatherStrategy } from "./api";
import RaceSelector from "./components/RaceSelector";
import DegradationChart from "./components/DegradationChart";
import StrategyControls from "./components/StrategyControls";
import StrategyList from "./components/StrategyList";
import LiveDashboard from "./components/LiveDashboard";
import styles from "./App.module.css";

/** The two modes the app can be in — "analysis" is the original view,
 *  "live" is the real-time race tracking dashboard. */
type AppMode = "analysis" | "live";

export default function App() {
  // -- App mode: "analysis" (default) or "live" --
  const [mode, setMode] = useState<AppMode>("analysis");

  // -- Race selection state --
  const [year, setYear] = useState(2024);
  const [schedule, setSchedule] = useState<ScheduleEvent[]>([]);
  const [grandPrix, setGrandPrix] = useState("");

  // -- Analysis results --
  const [degradation, setDegradation] = useState<DegradationResponse | null>(null);
  const [strategyResult, setStrategyResult] = useState<StrategyResponse | null>(null);

  // -- Strategy parameters --
  const [raceLaps, setRaceLaps] = useState(66);
  const [pitStopLoss, setPitStopLoss] = useState(22.0);

  // -- Weather state --
  // null = dry-only mode (uses the original GET endpoint)
  // WeatherWindow[] = custom weather (uses the POST endpoint)
  const [weatherWindows, setWeatherWindows] = useState<WeatherWindow[] | null>(null);

  // -- UI state --
  const [loadingDeg, setLoadingDeg] = useState(false);
  const [loadingStrat, setLoadingStrat] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When the year changes, fetch the season schedule to populate the GP dropdown.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const events = await fetchSchedule(year);
        if (cancelled) return;
        setSchedule(events);
        // Auto-select the first event
        if (events.length > 0) {
          setGrandPrix(events[0].event_name);
        }
        // Clear previous results when switching years
        setDegradation(null);
        setStrategyResult(null);
        setWeatherWindows(null);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load schedule");
        }
      }
    }

    load();
    // Cleanup: if the year changes before the fetch resolves, ignore the result
    return () => { cancelled = true; };
  }, [year]);

  // Fetch tyre degradation data for the selected GP
  async function handleAnalyze() {
    if (!grandPrix) return;
    setLoadingDeg(true);
    setError(null);
    setDegradation(null);
    setStrategyResult(null);
    // Reset weather when switching GPs so stale windows don't carry over
    setWeatherWindows(null);

    try {
      const data = await fetchDegradation(year, grandPrix);
      setDegradation(data);

      // Auto-populate race params from actual race data when available.
      // The backend computes these from the previous year's race session,
      // so users don't have to look up circuit-specific values manually.
      if (data.race_laps) setRaceLaps(data.race_laps);
      if (data.avg_pit_stop_loss_s) setPitStopLoss(data.avg_pit_stop_loss_s);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch degradation");
    } finally {
      setLoadingDeg(false);
    }
  }

  // Fetch ranked strategies — uses POST when weather windows are set, GET otherwise
  async function handleCalculateStrategy() {
    if (!grandPrix) return;
    setLoadingStrat(true);
    setError(null);

    try {
      let data: StrategyResponse;

      if (weatherWindows !== null) {
        // Mixed weather: use the POST endpoint with weather windows
        data = await fetchWeatherStrategy(year, grandPrix, {
          race_laps: raceLaps,
          pit_stop_loss: pitStopLoss,
          weather_windows: weatherWindows,
        });
      } else {
        // Dry-only: use the original GET endpoint
        data = await fetchStrategy(year, grandPrix, raceLaps, pitStopLoss);
      }

      setStrategyResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch strategies");
    } finally {
      setLoadingStrat(false);
    }
  }

  return (
    <div className={styles.app}>
      {/* Page header with mode toggle */}
      <header className={styles.header}>
        <div className={styles.headerRow}>
          {/* Mode toggle — pill-style buttons to switch between Analysis and Live */}
          <div className={styles.modeToggle}>
            <button
              className={`${styles.modeButton} ${mode === "analysis" ? styles.modeActive : ""}`}
              onClick={() => setMode("analysis")}
            >
              Analysis
            </button>
            <button
              className={`${styles.modeButton} ${mode === "live" ? styles.modeActive : ""}`}
              onClick={() => setMode("live")}
            >
              Live
            </button>
          </div>
          <h1 className={styles.title}>F1 Race Strategy</h1>
        </div>
        <p className={styles.subtitle}>
          {mode === "analysis"
            ? "Analyze tyre degradation and build pit stop strategies from real practice data"
            : "Track a live race session with real-time driver telemetry"}
        </p>
      </header>

      {/* Conditionally render the active mode */}
      {mode === "live" ? (
        <LiveDashboard />
      ) : (
        <>
          {/* Race selector: year + GP dropdowns + Analyze button */}
          <RaceSelector
            year={year}
            onYearChange={setYear}
            schedule={schedule}
            grandPrix={grandPrix}
            onGrandPrixChange={setGrandPrix}
            onAnalyze={handleAnalyze}
            loading={loadingDeg}
          />

          {/* Error banner */}
          {error && <div className={styles.error}>{error}</div>}

          {/* Loading indicator for degradation */}
          {loadingDeg && (
            <p className={styles.loading}>
              Loading practice data... (first load takes 1-2 minutes)
            </p>
          )}

          {/* Degradation chart — shows after Analyze is clicked */}
          {degradation && (
            <div className={styles.section}>
              <DegradationChart data={degradation} />
            </div>
          )}

          {/* Strategy controls — only show when degradation data exists */}
          {degradation && (
            <div className={styles.section}>
              <div className={styles.sectionLabel}>Strategy Parameters</div>
              <StrategyControls
                raceLaps={raceLaps}
                onRaceLapsChange={setRaceLaps}
                pitStopLoss={pitStopLoss}
                onPitStopLossChange={setPitStopLoss}
                onCalculate={handleCalculateStrategy}
                loading={loadingStrat}
                weatherWindows={weatherWindows}
                onWeatherWindowsChange={setWeatherWindows}
                raceDataLaps={degradation?.race_laps}
                raceDataPitLoss={degradation?.avg_pit_stop_loss_s}
              />
            </div>
          )}

          {/* Loading indicator for strategy */}
          {loadingStrat && (
            <p className={styles.loading}>
              Calculating strategies... (this can take 30-60 seconds)
            </p>
          )}

          {/* Strategy list — shows after Calculate is clicked */}
          {strategyResult && (
            <div className={styles.section}>
              <StrategyList data={strategyResult} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
