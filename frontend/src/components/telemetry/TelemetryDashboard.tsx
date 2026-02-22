/**
 * TelemetryDashboard — main live race view.
 *
 * Two states:
 *  1. Pre-connection: race selection UI (year, GP, team dropdowns + "Go Live")
 *  2. Connected: header bar, two DriverPanels side-by-side with LapDelta,
 *     TrackMap at the bottom, and collapsible race control log.
 *
 * Replaces the old LiveDashboard end-to-end.  The connection flow is the same:
 *   fetchSchedule → fetchLiveDrivers → fetchLiveStatus → startLiveTracking → SSE
 */

import { useState, useEffect, useMemo } from "react";
import { Activity } from "lucide-react";
import type {
  ScheduleEvent,
  LiveDriver,
  LiveTeam,
  LiveRaceState,
  LiveStrategy,
  RaceControlMessage,
} from "../../types";
import {
  fetchSchedule,
  fetchLiveStatus,
  fetchLiveDrivers,
  startLiveTracking,
} from "../../api";
import { useLiveRace } from "../../hooks/useLiveRace";
import RaceStatusBadge from "./RaceStatusBadge";
import TeamSelector from "./TeamSelector";
import LapDelta from "./LapDelta";
import DriverPanel from "./DriverPanel";
import TrackMap from "./TrackMap";

const YEARS = [2025, 2024, 2023];

export default function TelemetryDashboard() {
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

  // -- Race control log visibility --
  const [logOpen, setLogOpen] = useState(false);

  // SSE connection
  const raceState: LiveRaceState = useLiveRace(sessionKey);

  // Fetch schedule when year changes
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const events = await fetchSchedule(year);
        if (cancelled) return;
        setSchedule(events);
        if (events.length > 0) setGrandPrix(events[0].event_name);
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

  // Fetch teams when GP changes
  useEffect(() => {
    if (!grandPrix) return;
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchLiveDrivers(year, grandPrix);
        if (cancelled) return;
        setTeams(data.teams);
        if (data.teams.length > 0) setSelectedTeam(data.teams[0].team);
      } catch {
        // Team list will be empty — not critical
      }
    }
    load();
    return () => { cancelled = true; };
  }, [year, grandPrix]);

  // "Go Live" handler
  async function handleGo() {
    if (!grandPrix) return;
    setConnecting(true);
    setError(null);
    try {
      const status = await fetchLiveStatus(year, grandPrix);
      if (!status.session_key) {
        setError("No race session found. The session may not have started yet.");
        return;
      }
      const laps = status.total_laps ?? 66;
      setTotalLaps(laps);
      await startLiveTracking(status.session_key, laps, year, grandPrix);
      setSessionKey(status.session_key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect");
    } finally {
      setConnecting(false);
    }
  }

  // Selected team's two drivers from the live state
  const teamDrivers: LiveDriver[] = useMemo(() => {
    if (!selectedTeam || !raceState.drivers) return [];
    return Object.values(raceState.drivers)
      .filter((d) => d.team === selectedTeam)
      .sort((a, b) => a.position - b.position);
  }, [selectedTeam, raceState.drivers]);

  // Team colour for styling
  const teamColor = useMemo(() => {
    const team = teams.find((t) => t.team === selectedTeam);
    return team?.team_color ?? "#888888";
  }, [teams, selectedTeam]);

  // Strategy recommendations per driver
  const driverStrategies: Record<number, LiveStrategy[]> = useMemo(() => {
    return raceState.strategies ?? {};
  }, [raceState.strategies]);

  // Race control log (most recent first)
  const recentMessages: RaceControlMessage[] = useMemo(() => {
    return [...raceState.race_control_log].reverse().slice(0, 20);
  }, [raceState.race_control_log]);

  // Selected team's driver numbers (for track map highlighting)
  const selectedDriverNums = useMemo(() => {
    return teamDrivers.map((d) => d.driver_number);
  }, [teamDrivers]);

  // -----------------------------------------------------------------------
  // Pre-connection: race selection UI
  // -----------------------------------------------------------------------
  if (!sessionKey) {
    return (
      <div className="min-h-[400px] bg-f1-black p-6 rounded-lg border border-f1-border">
        <div className="max-w-md mx-auto space-y-6">
          <div className="text-center space-y-2">
            <Activity className="w-10 h-10 text-f1-red mx-auto" />
            <h2 className="text-xl font-f1 font-bold text-f1-white">Live Telemetry</h2>
            <p className="text-sm text-f1-muted font-f1">
              Select a race and team to start tracking
            </p>
          </div>

          {/* Year selector */}
          <div className="space-y-1">
            <label htmlFor="tele-year" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Season
            </label>
            <select
              id="tele-year"
              value={year}
              onChange={(e) => setYear(Number(e.target.value))}
              className="w-full bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-3 py-2 focus:outline-none focus:border-f1-red"
            >
              {YEARS.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>

          {/* GP selector */}
          <div className="space-y-1">
            <label htmlFor="tele-gp" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Grand Prix
            </label>
            <select
              id="tele-gp"
              value={grandPrix}
              onChange={(e) => setGrandPrix(e.target.value)}
              disabled={schedule.length === 0}
              className="w-full bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-3 py-2 focus:outline-none focus:border-f1-red"
            >
              {schedule.length === 0 && <option value="">Loading...</option>}
              {schedule.map((ev) => (
                <option key={ev.round_number} value={ev.event_name}>
                  {ev.event_name} ({ev.country})
                </option>
              ))}
            </select>
          </div>

          {/* Team selector */}
          <div className="space-y-1">
            <label htmlFor="tele-team" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Team
            </label>
            <select
              id="tele-team"
              value={selectedTeam}
              onChange={(e) => setSelectedTeam(e.target.value)}
              disabled={teams.length === 0}
              className="w-full bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-3 py-2 focus:outline-none focus:border-f1-red"
            >
              {teams.length === 0 && <option value="">Select GP first</option>}
              {teams.map((t) => (
                <option key={t.team} value={t.team}>{t.team}</option>
              ))}
            </select>
          </div>

          {/* Go Live button */}
          <button
            onClick={handleGo}
            disabled={connecting || !grandPrix}
            className="w-full bg-f1-red hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-f1 font-semibold py-2.5 rounded transition-colors"
          >
            {connecting ? "Connecting..." : "Go Live"}
          </button>

          {error && (
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm font-f1 px-3 py-2 rounded">
              {error}
            </div>
          )}
        </div>
      </div>
    );
  }

  // -----------------------------------------------------------------------
  // Connected: full telemetry dashboard
  // -----------------------------------------------------------------------
  return (
    <div className="bg-f1-black rounded-lg border border-f1-border overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-f1-card border-b border-f1-border">
        <div className="flex items-center gap-3">
          <Activity className="w-4 h-4 text-f1-red" />
          <span className="text-sm font-f1 font-bold text-f1-white tracking-wider uppercase">
            Live Telemetry
          </span>
          <RaceStatusBadge
            isSafetyCar={raceState.is_safety_car}
            lastMessage={raceState.last_race_control_message}
          />
        </div>
        <div className="flex items-center gap-4">
          <TeamSelector
            teams={teams}
            selectedTeam={selectedTeam}
            onChange={setSelectedTeam}
          />
          {/* Connection indicator */}
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${
                raceState.connected ? "bg-f1-green animate-pulse-fast" : "bg-red-500"
              }`}
            />
            <span className="text-[10px] text-f1-muted font-f1 uppercase">
              {raceState.connected ? "Live" : "Offline"}
            </span>
          </div>
        </div>
      </div>

      {/* Main content area */}
      <div className="p-3">
        {teamDrivers.length > 0 ? (
          <>
            {/* Two driver panels with lap delta divider */}
            <div className="flex gap-3 mb-3">
              <DriverPanel
                driver={teamDrivers[0]}
                telemetry={raceState.car_data?.[teamDrivers[0].driver_number]}
                telemetryAvailable={raceState.telemetry_available}
                teamColor={teamColor}
                strategies={driverStrategies[teamDrivers[0].driver_number] ?? []}
              />
              <LapDelta
                driver1={teamDrivers[0]}
                driver2={teamDrivers[1]}
                currentLap={raceState.current_lap}
                totalLaps={raceState.total_laps || totalLaps || 0}
              />
              {teamDrivers[1] && (
                <DriverPanel
                  driver={teamDrivers[1]}
                  telemetry={raceState.car_data?.[teamDrivers[1].driver_number]}
                  telemetryAvailable={raceState.telemetry_available}
                  teamColor={teamColor}
                  strategies={driverStrategies[teamDrivers[1].driver_number] ?? []}
                />
              )}
            </div>

            {/* Track map — shows all 20 drivers, selected team highlighted */}
            {raceState.telemetry_available && Object.keys(raceState.car_data).length > 0 && (
              <div className="h-52 bg-f1-card border border-f1-border rounded-lg p-2 mb-3">
                <TrackMap
                  drivers={raceState.drivers}
                  carData={raceState.car_data}
                  selectedDrivers={selectedDriverNums}
                />
              </div>
            )}
          </>
        ) : (
          <div className="py-12 text-center text-f1-muted font-f1 text-sm">
            Waiting for driver data...
          </div>
        )}

        {/* Collapsible race control log */}
        {recentMessages.length > 0 && (
          <div className="border-t border-f1-border pt-2">
            <button
              onClick={() => setLogOpen(!logOpen)}
              className="flex items-center gap-2 text-xs text-f1-muted font-f1 uppercase tracking-wider hover:text-f1-white transition-colors w-full"
            >
              <span className={`transition-transform ${logOpen ? "rotate-90" : ""}`}>
                ▶
              </span>
              Race Control ({recentMessages.length})
            </button>
            {logOpen && (
              <ul className="mt-2 space-y-1 max-h-40 overflow-y-auto">
                {recentMessages.map((msg, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2 text-xs font-f1"
                  >
                    <span className="text-f1-muted w-12 flex-shrink-0">
                      {msg.lap !== null ? `L${msg.lap}` : "—"}
                    </span>
                    <span className="text-f1-white">{msg.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
