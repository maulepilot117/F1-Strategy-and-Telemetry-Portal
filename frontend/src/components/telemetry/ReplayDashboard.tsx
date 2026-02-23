/**
 * ReplayDashboard — replay a historical race through the telemetry dashboard.
 *
 * Two states:
 *  1. Pre-connection: year/GP/team selection + speed selector + "Start Replay"
 *  2. Connected: same ConnectedDashboard as live, plus replay-specific controls
 *     (speed selector, progress bar, play/pause, stop)
 *
 * The replay flow:
 *  fetchSchedule → fetchLiveDrivers → fetchLiveStatus → startReplay → SSE
 */

import { useState, useEffect } from "react";
import { Play, Pause, Square, RotateCcw } from "lucide-react";
import type { ScheduleEvent, LiveTeam, LiveRaceState } from "../../types";
import {
  fetchSchedule,
  fetchLiveStatus,
  fetchLiveDrivers,
  startReplay,
  setReplaySpeed,
  stopReplay,
} from "../../api";
import { useLiveRace } from "../../hooks/useLiveRace";
import ConnectedDashboard from "./ConnectedDashboard";

const YEARS = [2025, 2024, 2023];
const SPEEDS = [1, 2, 4, 8];

export default function ReplayDashboard() {
  // -- Race selection --
  const [year, setYear] = useState(2024);
  const [schedule, setSchedule] = useState<ScheduleEvent[]>([]);
  const [grandPrix, setGrandPrix] = useState("");

  // -- Team selection --
  const [teams, setTeams] = useState<LiveTeam[]>([]);
  const [selectedTeam, setSelectedTeam] = useState("");

  // -- Replay state --
  const [sessionKey, setSessionKey] = useState<number | null>(null);
  const [totalLaps, setTotalLaps] = useState<number | null>(null);
  const [speed, setSpeed] = useState(4);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // Track the speed before pausing so we can restore it
  const [pausedSpeed, setPausedSpeed] = useState<number | null>(null);

  // SSE connection — same hook as live
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

  // Start replay handler
  async function handleStartReplay() {
    if (!grandPrix) return;
    setConnecting(true);
    setError(null);
    try {
      // Resolve session_key from OpenF1
      const status = await fetchLiveStatus(year, grandPrix);
      if (!status.session_key) {
        setError("No race session found for this GP.");
        return;
      }
      const laps = status.total_laps ?? 66;
      setTotalLaps(laps);

      // Start the replay on the backend
      await startReplay(status.session_key, laps, year, grandPrix, speed);

      // Set sessionKey to trigger SSE connection via useLiveRace
      setSessionKey(status.session_key);
      setPausedSpeed(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start replay");
    } finally {
      setConnecting(false);
    }
  }

  // Speed change handler (during replay)
  async function handleSpeedChange(newSpeed: number) {
    setSpeed(newSpeed);
    setPausedSpeed(null);
    try {
      await setReplaySpeed(newSpeed);
    } catch {
      // Speed will update on next cycle anyway
    }
  }

  // Play/Pause toggle
  async function handlePlayPause() {
    if (raceState.replay_speed === 0) {
      // Resume — restore previous speed
      const resumeSpeed = pausedSpeed ?? speed;
      setPausedSpeed(null);
      setSpeed(resumeSpeed);
      try {
        await setReplaySpeed(resumeSpeed);
      } catch { /* noop */ }
    } else {
      // Pause — save current speed and set to 0
      setPausedSpeed(raceState.replay_speed);
      try {
        await setReplaySpeed(0);
      } catch { /* noop */ }
    }
  }

  // Stop handler
  async function handleStop() {
    try {
      await stopReplay();
    } catch { /* noop */ }
    setSessionKey(null);
    setPausedSpeed(null);
  }

  // -----------------------------------------------------------------------
  // Pre-connection: race selection + speed selector
  // -----------------------------------------------------------------------
  if (!sessionKey) {
    return (
      <div className="min-h-[400px] bg-f1-black p-6 rounded-lg border border-f1-border">
        <div className="max-w-md mx-auto space-y-6">
          <div className="text-center space-y-2">
            <RotateCcw className="w-10 h-10 text-f1-red mx-auto" />
            <h2 className="text-xl font-f1 font-bold text-f1-white">Race Replay</h2>
            <p className="text-sm text-f1-muted font-f1">
              Replay a past race through the telemetry dashboard
            </p>
          </div>

          {/* Year selector */}
          <div className="space-y-1">
            <label htmlFor="replay-year" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Season
            </label>
            <select
              id="replay-year"
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
            <label htmlFor="replay-gp" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Grand Prix
            </label>
            <select
              id="replay-gp"
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
            <label htmlFor="replay-team" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Team
            </label>
            <select
              id="replay-team"
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

          {/* Speed selector */}
          <div className="space-y-1">
            <label className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
              Playback Speed
            </label>
            <div className="flex gap-2">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  onClick={() => setSpeed(s)}
                  className={`flex-1 py-2 text-sm font-f1 font-semibold rounded border transition-colors ${
                    speed === s
                      ? "bg-f1-red border-f1-red text-white"
                      : "bg-f1-dark border-f1-border text-f1-muted hover:text-f1-white hover:border-f1-white/30"
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>

          {/* Start Replay button */}
          <button
            onClick={handleStartReplay}
            disabled={connecting || !grandPrix}
            className="w-full bg-f1-red hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-f1 font-semibold py-2.5 rounded transition-colors"
          >
            {connecting ? "Loading race data..." : "Start Replay"}
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
  // Connected: ConnectedDashboard + replay controls
  // -----------------------------------------------------------------------
  const isPaused = raceState.replay_speed === 0;

  const replayControls = (
    <div className="flex items-center gap-3">
      {/* Speed selector */}
      <div className="flex items-center gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => handleSpeedChange(s)}
            className={`px-2 py-0.5 text-[10px] font-f1 font-bold rounded transition-colors ${
              speed === s && !isPaused
                ? "bg-f1-red text-white"
                : "text-f1-muted hover:text-f1-white"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>

      {/* Progress bar */}
      <div className="w-32 h-1.5 bg-f1-dark rounded-full overflow-hidden" title={`${Math.round(raceState.replay_elapsed_pct)}%`}>
        <div
          className="h-full bg-f1-red rounded-full transition-all duration-1000"
          style={{ width: `${raceState.replay_elapsed_pct}%` }}
        />
      </div>

      {/* Play/Pause */}
      <button
        onClick={handlePlayPause}
        className="text-f1-muted hover:text-f1-white transition-colors"
        title={isPaused ? "Resume" : "Pause"}
      >
        {isPaused
          ? <Play className="w-3.5 h-3.5" />
          : <Pause className="w-3.5 h-3.5" />}
      </button>

      {/* Stop */}
      <button
        onClick={handleStop}
        className="text-f1-muted hover:text-red-400 transition-colors"
        title="Stop replay"
      >
        <Square className="w-3.5 h-3.5" />
      </button>
    </div>
  );

  return (
    <ConnectedDashboard
      raceState={raceState}
      teams={teams}
      selectedTeam={selectedTeam}
      onTeamChange={setSelectedTeam}
      totalLaps={totalLaps}
      headerLabel="Replay"
      headerExtra={replayControls}
    />
  );
}
