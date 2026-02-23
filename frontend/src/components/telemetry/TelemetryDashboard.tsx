/**
 * TelemetryDashboard — main live race view.
 *
 * Two states:
 *  1. Pre-connection: auto-detects whether a race is live. If yes, shows
 *     "Race in progress" and auto-connects. If no, shows a countdown to
 *     the next upcoming race with track name and time remaining.
 *  2. Connected: delegates to ConnectedDashboard (shared with ReplayDashboard).
 *
 * The connection flow:
 *   fetchSchedule → auto-detect → fetchLiveDrivers → fetchLiveStatus → startLiveTracking → SSE
 */

import { useState, useEffect, useMemo, useCallback } from "react";
import { Activity, Clock } from "lucide-react";
import type {
  ScheduleEvent,
  LiveTeam,
  LiveRaceState,
} from "../../types";
import {
  fetchSchedule,
  fetchLiveStatus,
  fetchLiveDrivers,
  startLiveTracking,
} from "../../api";
import { useLiveRace } from "../../hooks/useLiveRace";
import ConnectedDashboard from "./ConnectedDashboard";

/** How long after race_date_utc we consider a race to be "live" (3 hours) */
const RACE_WINDOW_MS = 3 * 60 * 60 * 1000;

/** Format a duration in ms as "Xd Xh Xm Xs" */
function formatCountdown(ms: number): string {
  if (ms <= 0) return "Starting now";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0 || days > 0) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  parts.push(`${seconds}s`);
  return parts.join(" ");
}

export default function TelemetryDashboard() {
  // -- Schedule + auto-detection --
  const [schedule, setSchedule] = useState<ScheduleEvent[]>([]);
  const [scheduleLoading, setScheduleLoading] = useState(true);

  // -- Team selection --
  const [teams, setTeams] = useState<LiveTeam[]>([]);
  const [selectedTeam, setSelectedTeam] = useState("");

  // -- Live tracking state --
  const [sessionKey, setSessionKey] = useState<number | null>(null);
  const [totalLaps, setTotalLaps] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // -- Countdown timer --
  const [now, setNow] = useState(Date.now());

  // SSE connection
  const raceState: LiveRaceState = useLiveRace(sessionKey);

  // Tick the clock every second for the countdown display
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Fetch current year's schedule on mount
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setScheduleLoading(true);
      try {
        const currentYear = new Date().getFullYear();
        const events = await fetchSchedule(currentYear);
        if (cancelled) return;
        setSchedule(events);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load schedule");
        }
      } finally {
        if (!cancelled) setScheduleLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // Find the race that's currently live or the next upcoming race
  const { liveRace, nextRace, countdownMs } = useMemo(() => {
    const currentTime = now;
    let liveRace: ScheduleEvent | null = null;
    let nextRace: ScheduleEvent | null = null;
    let countdownMs = 0;

    for (const event of schedule) {
      if (!event.race_date_utc) continue;
      const raceStart = new Date(event.race_date_utc).getTime();
      const raceEnd = raceStart + RACE_WINDOW_MS;

      if (currentTime >= raceStart && currentTime <= raceEnd) {
        // Race is happening right now
        liveRace = event;
        break;
      }

      if (raceStart > currentTime) {
        // This is the next upcoming race
        nextRace = event;
        countdownMs = raceStart - currentTime;
        break;
      }
    }

    return { liveRace, nextRace, countdownMs };
  }, [schedule, now]);

  // Derive the relevant GP name for team loading
  const relevantGP = liveRace?.event_name ?? nextRace?.event_name ?? "";
  const relevantYear = new Date().getFullYear();

  // Fetch teams when we know which GP to show
  useEffect(() => {
    if (!relevantGP) return;
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchLiveDrivers(relevantYear, relevantGP);
        if (cancelled) return;
        setTeams(data.teams);
        if (data.teams.length > 0) setSelectedTeam(data.teams[0].team);
      } catch {
        // Team list will be empty — not critical
      }
    }
    load();
    return () => { cancelled = true; };
  }, [relevantGP, relevantYear]);

  // Connect to a live race — called automatically or via button
  const handleConnect = useCallback(async (event: ScheduleEvent) => {
    setConnecting(true);
    setError(null);
    try {
      const status = await fetchLiveStatus(relevantYear, event.event_name);
      if (!status.session_key) {
        setError("No race session found. The session may not have started yet.");
        return;
      }
      const laps = status.total_laps ?? 66;
      setTotalLaps(laps);
      await startLiveTracking(status.session_key, laps, relevantYear, event.event_name);
      setSessionKey(status.session_key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect");
    } finally {
      setConnecting(false);
    }
  }, [relevantYear]);

  // -----------------------------------------------------------------------
  // Connected: delegate to ConnectedDashboard
  // -----------------------------------------------------------------------
  if (sessionKey) {
    return (
      <ConnectedDashboard
        raceState={raceState}
        teams={teams}
        selectedTeam={selectedTeam}
        onTeamChange={setSelectedTeam}
        totalLaps={totalLaps}
      />
    );
  }

  // -----------------------------------------------------------------------
  // Pre-connection: auto-detection + countdown
  // -----------------------------------------------------------------------
  return (
    <div className="min-h-[400px] bg-f1-black p-6 rounded-lg border border-f1-border">
      <div className="max-w-md mx-auto space-y-6">
        {scheduleLoading ? (
          // Loading state
          <div className="text-center py-12">
            <Activity className="w-10 h-10 text-f1-red mx-auto animate-pulse" />
            <p className="text-sm text-f1-muted font-f1 mt-4">
              Loading race calendar...
            </p>
          </div>
        ) : liveRace ? (
          // Race is happening now — prompt to connect
          <>
            <div className="text-center space-y-2">
              <Activity className="w-10 h-10 text-f1-green mx-auto animate-pulse" />
              <h2 className="text-xl font-f1 font-bold text-f1-white">Race In Progress</h2>
              <p className="text-lg font-f1 text-f1-red font-semibold">
                {liveRace.event_name}
              </p>
              <p className="text-sm text-f1-muted font-f1">
                {liveRace.location}, {liveRace.country}
              </p>
            </div>

            {/* Team selector */}
            <div className="space-y-1">
              <label htmlFor="live-team" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
                Team
              </label>
              <select
                id="live-team"
                value={selectedTeam}
                onChange={(e) => setSelectedTeam(e.target.value)}
                disabled={teams.length === 0}
                className="w-full bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-3 py-2 focus:outline-none focus:border-f1-red"
              >
                {teams.length === 0 && <option value="">Loading teams...</option>}
                {teams.map((t) => (
                  <option key={t.team} value={t.team}>{t.team}</option>
                ))}
              </select>
            </div>

            {/* Connect button */}
            <button
              onClick={() => handleConnect(liveRace)}
              disabled={connecting}
              className="w-full bg-f1-green hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed text-black font-f1 font-semibold py-2.5 rounded transition-all"
            >
              {connecting ? "Connecting..." : "Connect to Live Race"}
            </button>
          </>
        ) : nextRace ? (
          // No live race — show countdown to next
          <>
            <div className="text-center space-y-3">
              <Clock className="w-10 h-10 text-f1-muted mx-auto" />
              <h2 className="text-xl font-f1 font-bold text-f1-white">Next Race</h2>
              <p className="text-lg font-f1 text-f1-red font-semibold">
                {nextRace.event_name}
              </p>
              <p className="text-sm text-f1-muted font-f1">
                {nextRace.location}, {nextRace.country}
              </p>

              {/* Countdown display */}
              <div className="bg-f1-card border border-f1-border rounded-lg py-4 px-6 mt-4">
                <p className="text-3xl font-f1 font-bold text-f1-white tracking-wider">
                  {formatCountdown(countdownMs)}
                </p>
                <p className="text-xs text-f1-muted font-f1 mt-1">
                  {nextRace.race_date_utc
                    ? new Date(nextRace.race_date_utc).toLocaleDateString(undefined, {
                        weekday: "long",
                        month: "long",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        timeZoneName: "short",
                      })
                    : nextRace.date}
                </p>
              </div>
            </div>

            {/* Pre-select team while waiting */}
            <div className="space-y-1">
              <label htmlFor="next-team" className="text-xs text-f1-muted font-f1 uppercase tracking-wider">
                Pre-select Team
              </label>
              <select
                id="next-team"
                value={selectedTeam}
                onChange={(e) => setSelectedTeam(e.target.value)}
                disabled={teams.length === 0}
                className="w-full bg-f1-dark text-f1-white text-sm font-f1 border border-f1-border rounded px-3 py-2 focus:outline-none focus:border-f1-red"
              >
                {teams.length === 0 && <option value="">Loading teams...</option>}
                {teams.map((t) => (
                  <option key={t.team} value={t.team}>{t.team}</option>
                ))}
              </select>
            </div>
          </>
        ) : (
          // Season over or no upcoming races
          <div className="text-center space-y-2 py-8">
            <Activity className="w-10 h-10 text-f1-muted mx-auto" />
            <h2 className="text-xl font-f1 font-bold text-f1-white">No Upcoming Races</h2>
            <p className="text-sm text-f1-muted font-f1">
              The current season has ended. Use the Replay tab to watch past races.
            </p>
          </div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm font-f1 px-3 py-2 rounded">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
