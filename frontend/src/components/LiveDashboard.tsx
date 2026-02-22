/**
 * LiveDashboard — live race tracking view.
 *
 * Shows two drivers from the selected team side-by-side, with real-time
 * position, tyre compound, gap, lap times, and pit stop history.
 * Data flows in via SSE from the backend (useLiveRace hook).
 *
 * Phase 1 focuses on display only — strategy recalculation is Phase 2.
 */

import { useState, useEffect, useMemo } from "react";
import type {
  ScheduleEvent,
  LiveDriver,
  LiveTeam,
  LiveRaceState,
  LiveStrategy,
  RaceControlMessage,
} from "../types";
import { fetchSchedule, fetchLiveStatus, fetchLiveDrivers, startLiveTracking } from "../api";
import { useLiveRace } from "../hooks/useLiveRace";
import styles from "./LiveDashboard.module.css";

/* Available seasons — same list as the analysis mode */
const YEARS = [2025, 2024, 2023];

/**
 * Map a tyre compound name to its broadcast colour.
 * Returns a CSS colour string for inline styling on the tyre badge.
 */
function compoundColor(compound: string): string {
  switch (compound.toUpperCase()) {
    case "SOFT":
      return "var(--compound-soft)";
    case "MEDIUM":
      return "var(--compound-medium)";
    case "HARD":
      return "var(--compound-hard)";
    case "INTERMEDIATE":
      return "var(--compound-intermediate)";
    case "WET":
      return "var(--compound-wet)";
    default:
      return "var(--text-secondary)";
  }
}

/** Format a lap time in seconds to M:SS.sss (e.g., 78.456 → 1:18.456) */
function formatLapTime(seconds: number | null): string {
  if (seconds === null || seconds <= 0) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(3);
  // Pad seconds to always show 2 digits before the decimal
  return `${mins}:${secs.padStart(6, "0")}`;
}

/** Format a gap value — leader shows "Leader", others show +X.XXXs */
function formatGap(gap: number): string {
  if (gap === 0) return "Leader";
  return `+${gap.toFixed(3)}s`;
}

// ---------------------------------------------------------------------------
// Driver card sub-component
// ---------------------------------------------------------------------------

interface DriverCardProps {
  driver: LiveDriver;
  teamColor: string;
}

function DriverCard({ driver, teamColor }: DriverCardProps) {
  return (
    <div className={styles.driverCard}>
      <div
        className={styles.driverCardHeader}
        style={{ borderBottomColor: `#${teamColor}` }}
      >
        <span className={styles.driverAbbr} style={{ color: `#${teamColor}` }}>
          {driver.abbreviation}
        </span>
        <span className={styles.driverTeam}>{driver.team}</span>
      </div>

      <div className={styles.driverStats}>
        {/* Position and gap */}
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Position</span>
          <span className={styles.statValue}>P{driver.position}</span>
        </div>
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Gap</span>
          <span className={styles.statValue}>{formatGap(driver.gap_to_leader)}</span>
        </div>
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Interval</span>
          <span className={styles.statValue}>
            {driver.interval === 0 ? "—" : `+${driver.interval.toFixed(3)}s`}
          </span>
        </div>

        {/* Tyre info */}
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Tyre</span>
          <span className={styles.statValue}>
            <span
              className={styles.tyreBadge}
              style={{
                backgroundColor: compoundColor(driver.current_compound),
                color: driver.current_compound.toUpperCase() === "HARD" ? "#000" : "#fff",
              }}
            >
              {driver.current_compound}
            </span>
          </span>
        </div>
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Tyre Age</span>
          <span className={styles.statValue}>{driver.tyre_age} laps</span>
        </div>

        {/* Lap time */}
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Last Lap</span>
          <span className={styles.statValue}>{formatLapTime(driver.last_lap_time)}</span>
        </div>

        {/* Pit stops */}
        <div className={styles.statRow}>
          <span className={styles.statLabel}>Pit Stops</span>
          <span className={styles.statValue}>{driver.stops_completed}</span>
        </div>

        {/* Compounds used so far (e.g., SOFT → MEDIUM → HARD) */}
        {driver.compounds_used.length > 0 && (
          <div className={styles.statRow}>
            <span className={styles.statLabel}>Stints</span>
            <span className={styles.statValue}>
              {driver.compounds_used.map((c, i) => (
                <span key={i}>
                  {i > 0 && " → "}
                  <span
                    className={styles.tyreBadge}
                    style={{
                      backgroundColor: compoundColor(c),
                      color: c.toUpperCase() === "HARD" ? "#000" : "#fff",
                      fontSize: "0.7rem",
                    }}
                  >
                    {c.slice(0, 1)}
                  </span>
                </span>
              ))}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy panel sub-component — shows top 3 recommendations per driver
// ---------------------------------------------------------------------------

interface StrategyPanelProps {
  driver: LiveDriver;
  strategies: LiveStrategy[];
  teamColor: string;
}

function StrategyPanel({ driver, strategies, teamColor }: StrategyPanelProps) {
  if (strategies.length === 0) return null;

  return (
    <div className={styles.strategyPanel}>
      <div className={styles.strategyHeader} style={{ borderBottomColor: `#${teamColor}` }}>
        <span className={styles.strategyDriverLabel} style={{ color: `#${teamColor}` }}>
          {driver.abbreviation}
        </span>
        <span className={styles.strategyTitle}>Recommended Strategy</span>
      </div>

      {strategies.map((strat, i) => (
        <div key={i} className={styles.strategyCard}>
          <div className={styles.strategyRank}>
            <span className={styles.rankBadge}>#{strat.rank}</span>
            <span className={styles.strategyName}>{strat.name}</span>
            {strat.gap_to_best_s > 0 && (
              <span className={styles.strategyGap}>+{strat.gap_to_best_s.toFixed(1)}s</span>
            )}
          </div>
          <div className={styles.strategyStints}>
            {strat.stints.map((stint, j) => (
              <span key={j} className={styles.strategyStint}>
                {j > 0 && <span className={styles.pitArrow}>→</span>}
                <span
                  className={styles.tyreBadge}
                  style={{
                    backgroundColor: compoundColor(stint.compound),
                    color: stint.compound.toUpperCase() === "HARD" ? "#000" : "#fff",
                    fontSize: "0.7rem",
                  }}
                >
                  {stint.compound.slice(0, 1)}
                </span>
                <span className={styles.stintLaps}>
                  L{stint.start_lap}–{stint.end_lap}
                </span>
              </span>
            ))}
          </div>
          {strat.pit_laps.length > 0 && (
            <div className={styles.pitWindow}>
              Pit: {strat.pit_laps.map((l) => `lap ${l}`).join(", ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main LiveDashboard component
// ---------------------------------------------------------------------------

export default function LiveDashboard() {
  // -- Race selection --
  const [year, setYear] = useState(2025);
  const [schedule, setSchedule] = useState<ScheduleEvent[]>([]);
  const [grandPrix, setGrandPrix] = useState("");

  // -- Team selection --
  const [teams, setTeams] = useState<LiveTeam[]>([]);
  const [selectedTeam, setSelectedTeam] = useState("");

  // -- Live tracking state --
  const [sessionKey, setSessionKey] = useState<number | null>(null);
  const [totalLaps, setTotalLaps] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // SSE connection — null sessionKey means disconnected
  const raceState: LiveRaceState = useLiveRace(sessionKey);

  // Fetch schedule when year changes (same pattern as App.tsx)
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const events = await fetchSchedule(year);
        if (cancelled) return;
        setSchedule(events);
        if (events.length > 0) setGrandPrix(events[0].event_name);
        // Clear previous state when switching years
        setSessionKey(null);
        setTeams([]);
        setSelectedTeam("");
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load schedule");
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [year]);

  // When GP changes, fetch the teams/drivers for the team dropdown
  useEffect(() => {
    if (!grandPrix) return;
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchLiveDrivers(year, grandPrix);
        if (cancelled) return;
        setTeams(data.teams);
        // Auto-select the first team
        if (data.teams.length > 0) setSelectedTeam(data.teams[0].team);
      } catch {
        // Not critical — the dropdown will just be empty until the user retries
      }
    }
    load();
    return () => { cancelled = true; };
  }, [year, grandPrix]);

  // "Go" button handler — resolve session, start polling, connect SSE
  async function handleGo() {
    if (!grandPrix) return;
    setConnecting(true);
    setError(null);

    try {
      // Step 1: Resolve the session key and total laps from the backend
      const status = await fetchLiveStatus(year, grandPrix);

      if (!status.session_key) {
        setError("No race session found for this GP. The session may not have started yet.");
        return;
      }

      const laps = status.total_laps ?? 66; // fallback if we can't determine lap count
      setTotalLaps(laps);

      // Step 2: Tell the backend to start polling OpenF1.
      // Pass year and grandPrix so the backend can run mid-race strategy
      // recalculation using practice degradation data for this circuit.
      await startLiveTracking(status.session_key, laps, year, grandPrix);

      // Step 3: Connect the SSE stream (triggers useLiveRace)
      setSessionKey(status.session_key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect to live session");
    } finally {
      setConnecting(false);
    }
  }

  // Find the two drivers for the selected team from the live race state.
  // We match by team name from the teams dropdown against driver.team in the race state.
  const teamDrivers: LiveDriver[] = useMemo(() => {
    if (!selectedTeam || !raceState.drivers) return [];
    return Object.values(raceState.drivers)
      .filter((d) => d.team === selectedTeam)
      .sort((a, b) => a.position - b.position);
  }, [selectedTeam, raceState.drivers]);

  // Get the team colour from the teams list (for card styling)
  const teamColor = useMemo(() => {
    const team = teams.find((t) => t.team === selectedTeam);
    return team?.team_color ?? "888888";
  }, [teams, selectedTeam]);

  // Per-driver strategies from the SSE state (keyed by driver_number)
  const driverStrategies: Record<number, LiveStrategy[]> = useMemo(() => {
    return raceState.strategies ?? {};
  }, [raceState.strategies]);

  // Race control log — most recent first
  const recentMessages: RaceControlMessage[] = useMemo(() => {
    return [...raceState.race_control_log].reverse().slice(0, 20);
  }, [raceState.race_control_log]);

  return (
    <div className={styles.container}>
      {/* Race + Team selector row */}
      <div className={styles.selectorRow}>
        {/* Year */}
        <div className={styles.field}>
          <label htmlFor="live-year">Season</label>
          <select
            id="live-year"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
          >
            {YEARS.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>

        {/* Grand Prix */}
        <div className={styles.field}>
          <label htmlFor="live-gp">Grand Prix</label>
          <select
            id="live-gp"
            value={grandPrix}
            onChange={(e) => setGrandPrix(e.target.value)}
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

        {/* Team */}
        <div className={styles.field}>
          <label htmlFor="live-team">Team</label>
          <select
            id="live-team"
            value={selectedTeam}
            onChange={(e) => setSelectedTeam(e.target.value)}
            disabled={teams.length === 0}
          >
            {teams.length === 0 && <option value="">Select GP first</option>}
            {teams.map((t) => (
              <option key={t.team} value={t.team}>{t.team}</option>
            ))}
          </select>
        </div>

        {/* Go button — resolves session and starts tracking */}
        <button
          className={styles.goButton}
          onClick={handleGo}
          disabled={connecting || !grandPrix}
        >
          {connecting ? "Connecting..." : "Go Live"}
        </button>
      </div>

      {/* Error banner */}
      {error && <div className={styles.error}>{error}</div>}

      {/* Before connection: empty state */}
      {!sessionKey && !connecting && !error && (
        <div className={styles.emptyState}>
          Select a race and team, then click "Go Live" to start tracking.
        </div>
      )}

      {/* After connection: status bar + driver cards + race control */}
      {sessionKey && (
        <>
          {/* Status bar */}
          <div className={styles.statusBar}>
            {/* Safety car badge */}
            <span
              className={`${styles.scBadge} ${
                raceState.is_safety_car ? styles.scActive : styles.scClear
              }`}
            >
              {raceState.is_safety_car ? "SC / VSC" : "Green"}
            </span>

            {/* Lap counter */}
            <span className={styles.lapCounter}>
              Lap {raceState.current_lap} / {raceState.total_laps || totalLaps || "—"}
            </span>

            {/* Connection indicator */}
            <span className={styles.connectionStatus}>
              <span
                className={`${styles.connectionDot} ${
                  raceState.connected ? styles.connected : styles.disconnected
                }`}
              />
              {raceState.connected ? "Connected" : "Reconnecting..."}
            </span>
          </div>

          {/* Driver cards — two columns for the selected team */}
          {teamDrivers.length > 0 ? (
            <div className={styles.driverGrid}>
              {teamDrivers.map((driver) => (
                <DriverCard
                  key={driver.driver_number}
                  driver={driver}
                  teamColor={teamColor}
                />
              ))}
            </div>
          ) : (
            <div className={styles.emptyState}>
              Waiting for driver data...
            </div>
          )}

          {/* Strategy recommendations — one panel per team driver */}
          {teamDrivers.length > 0 && Object.keys(driverStrategies).length > 0 && (
            <div className={styles.strategyGrid}>
              {teamDrivers.map((driver) => (
                <StrategyPanel
                  key={driver.driver_number}
                  driver={driver}
                  strategies={driverStrategies[driver.driver_number] ?? []}
                  teamColor={teamColor}
                />
              ))}
            </div>
          )}

          {/* Race control log */}
          {recentMessages.length > 0 && (
            <div className={styles.raceControlSection}>
              <div className={styles.sectionTitle}>Race Control</div>
              <ul className={styles.logList}>
                {recentMessages.map((msg, i) => (
                  <li key={i}>
                    <span className={styles.logLap}>
                      {msg.lap !== null ? `Lap ${msg.lap}` : "—"}
                    </span>
                    <span className={styles.logMessage}>{msg.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
