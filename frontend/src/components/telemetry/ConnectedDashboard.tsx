/**
 * ConnectedDashboard — shared connected-state telemetry view.
 *
 * Extracted from TelemetryDashboard so both the Live and Replay tabs
 * can render the same driver panels, track map, and race control log
 * without duplicating layout code.
 *
 * The parent provides:
 *  - raceState: the SSE-driven LiveRaceState
 *  - teams/selectedTeam: team data and selection
 *  - totalLaps: fallback for raceState.total_laps
 *  - headerExtra: optional React node for additional header controls (e.g., replay speed)
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
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-f1-card border-b border-f1-border">
        <div className="flex items-center gap-3">
          <Activity className="w-4 h-4 text-f1-red" />
          <span className="text-sm font-f1 font-bold text-f1-white tracking-wider uppercase">
            {headerLabel}
          </span>
          <RaceStatusBadge
            isSafetyCar={raceState.is_safety_car}
            lastMessage={raceState.last_race_control_message}
          />
        </div>
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
              className={`w-2 h-2 rounded-full ${
                raceState.connected ? "bg-f1-green animate-pulse-fast" : "bg-red-500"
              }`}
            />
            <span className="text-[10px] text-f1-muted font-f1 uppercase">
              {raceState.connected
                ? raceState.replay_mode ? "Replay" : "Live"
                : "Offline"}
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
