/**
 * ConnectedDashboard — shared connected-state telemetry view.
 *
 * Extracted from TelemetryDashboard so both the Live and Replay tabs
 * can render the same driver panels, track map, and race control log
 * without duplicating layout code.
 *
 * Header styled to match F1 broadcast aesthetic:
 *  F1 logo | LIVE TELEMETRY | Track status badge | [extra] | Team selector | Live dot
 */

import { useState, useMemo } from "react";
import { Activity } from "lucide-react";
import type {
  LiveDriver,
  LiveTeam,
  LiveRaceState,
  LiveStrategy,
  RaceControlMessage,
} from "../../types";
import { useAnimatedTelemetry } from "../../hooks/useAnimatedTelemetry";
import RaceStatusBadge from "./RaceStatusBadge";
import TeamSelector from "./TeamSelector";
import LapDelta from "./LapDelta";
import DriverPanel from "./DriverPanel";
import TrackMap from "./TrackMap";

interface ConnectedDashboardProps {
  raceState: LiveRaceState;
  teams: LiveTeam[];
  selectedTeam: string;
  onTeamChange: (team: string) => void;
  totalLaps: number | null;
  /** Optional extra controls rendered in the header bar (e.g., replay speed) */
  headerExtra?: React.ReactNode;
  /** Label shown next to the Activity icon (defaults to "Live Telemetry") */
  headerLabel?: string;
}

export default function ConnectedDashboard({
  raceState,
  teams,
  selectedTeam,
  onTeamChange,
  totalLaps,
  headerExtra,
  headerLabel = "Live Telemetry",
}: ConnectedDashboardProps) {
  const [logOpen, setLogOpen] = useState(false);

  // Animate through the car_data + location buffers at 60fps.
  // Returns the same Record<number, TelemetryData> shape as raceState.car_data,
  // but with smooth interpolation between SSE snapshots instead of 4s jumps.
  const animatedCarData = useAnimatedTelemetry(
    raceState.car_data_buffer,
    raceState.location_buffer,
    raceState.car_data,
  );

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

  return (
    <div className="bg-f1-black rounded-lg border border-f1-border overflow-hidden">
      {/* ── Header bar — broadcast style ── */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-f1-card border-b border-f1-border">
        {/* Left: logo + title + status */}
        <div className="flex items-center gap-3">
          <Activity className="w-5 h-5 text-f1-red" />
          <div className="flex flex-col">
            <span className="text-sm font-f1 font-bold text-f1-white tracking-wider uppercase leading-tight">
              {headerLabel}
            </span>
            <span className="text-[9px] font-f1 text-f1-muted tracking-widest uppercase">
              Official Data Stream
            </span>
          </div>
          <RaceStatusBadge
            isSafetyCar={raceState.is_safety_car}
            lastMessage={raceState.last_race_control_message}
          />
        </div>

        {/* Right: extra controls + team selector + connection indicator */}
        <div className="flex items-center gap-4">
          {headerExtra}
          <TeamSelector
            teams={teams}
            selectedTeam={selectedTeam}
            onChange={onTeamChange}
          />
          {/* Connection indicator */}
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2.5 h-2.5 rounded-full ${
                raceState.connected ? "bg-f1-green animate-pulse-fast" : "bg-red-500"
              }`}
            />
            <span className="text-[10px] text-f1-muted font-f1 uppercase font-semibold tracking-wider">
              {raceState.connected
                ? raceState.replay_mode ? "Replay" : "Live"
                : "Offline"}
            </span>
          </div>
        </div>
      </div>

      {/* ── Main content area ── */}
      <div className="p-3">
        {teamDrivers.length > 0 ? (
          <>
            {/* Two driver panels with gap/lap divider between them */}
            <div className="flex gap-2 mb-3">
              <DriverPanel
                driver={teamDrivers[0]}
                telemetry={animatedCarData[teamDrivers[0].driver_number]}
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
                  telemetry={animatedCarData[teamDrivers[1].driver_number]}
                  telemetryAvailable={raceState.telemetry_available}
                  teamColor={teamColor}
                  strategies={driverStrategies[teamDrivers[1].driver_number] ?? []}
                />
              )}
            </div>

            {/* Track map — shows all 20 drivers, selected team highlighted */}
            {raceState.telemetry_available && Object.keys(animatedCarData).length > 0 && (
              <div className="bg-f1-card border border-f1-border rounded-lg overflow-hidden mb-3">
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-f1-border">
                  <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1 font-semibold">
                    Track Position
                  </span>
                </div>
                <div className="h-52 p-2">
                  <TrackMap
                    drivers={raceState.drivers}
                    carData={animatedCarData}
                    selectedDrivers={selectedDriverNums}
                    trackOutline={raceState.track_outline}
                  />
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="py-12 text-center text-f1-muted font-f1 text-sm">
            Waiting for driver data...
          </div>
        )}

        {/* ── Collapsible race control log ── */}
        {recentMessages.length > 0 && (
          <div className="border-t border-f1-border pt-2">
            <button
              onClick={() => setLogOpen(!logOpen)}
              className="flex items-center gap-2 text-xs text-f1-muted font-f1 uppercase tracking-wider hover:text-f1-white transition-colors w-full"
            >
              <span className={`transition-transform ${logOpen ? "rotate-90" : ""}`}>
                &#9654;
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
