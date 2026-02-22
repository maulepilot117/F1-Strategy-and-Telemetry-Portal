/**
 * DriverPanel — combined telemetry + positional data for one driver.
 *
 * When telemetry_available is true (sponsor tier), shows:
 *   - Speed/RPM gauges, gear display, DRS, throttle/brake bars
 *   - Tyre compound + age, position, gap, interval
 *   - Condensed strategy recommendations
 *
 * When telemetry is unavailable (free tier), falls back to the
 * positional-only layout: position, gap, interval, tyre, stops.
 */

import type { LiveDriver, TelemetryData, LiveStrategy, Stint } from "../../types";
import CircularGauge from "./CircularGauge";
import GearDisplay from "./GearDisplay";
import DRSIndicator from "./DRSIndicator";
import BarGauge from "./BarGauge";
import TyreCompound from "./TyreCompound";
import TelemetryChart from "./TelemetryChart";

interface DriverPanelProps {
  driver: LiveDriver;
  telemetry: TelemetryData | undefined;
  telemetryAvailable: boolean;
  teamColor: string;
  strategies: LiveStrategy[];
}

/** Format gap value — leader shows "Leader", others show +X.XXXs */
function formatGap(gap: number): string {
  if (gap === 0) return "Leader";
  return `+${gap.toFixed(3)}s`;
}

/** Map compound name to a CSS colour for inline styling */
function compoundColor(compound: string): string {
  switch (compound.toUpperCase()) {
    case "SOFT": return "#ef4444";
    case "MEDIUM": return "#facc15";
    case "HARD": return "#ffffff";
    case "INTERMEDIATE": return "#22c55e";
    case "WET": return "#3b82f6";
    default: return "#9CA3AF";
  }
}

export default function DriverPanel({
  driver,
  telemetry,
  telemetryAvailable,
  teamColor,
  strategies,
}: DriverPanelProps) {
  return (
    <div className="bg-f1-card border border-f1-border rounded-lg overflow-hidden flex-1 min-w-0">
      {/* Header bar with team colour accent */}
      <div
        className="px-4 py-2 flex items-center justify-between"
        style={{ borderBottom: `2px solid ${teamColor}` }}
      >
        <div className="flex items-center gap-3">
          <span className="text-2xl font-f1 font-bold" style={{ color: teamColor }}>
            {driver.abbreviation}
          </span>
          <span className="text-xs text-f1-muted font-f1 hidden sm:inline">
            {driver.full_name}
          </span>
        </div>
        <div className="flex items-center gap-4 text-sm font-mono text-f1-white tabular-nums">
          <span>
            P<span className="text-lg font-f1 font-bold">{driver.position}</span>
          </span>
          <span className="text-f1-muted text-xs">
            {formatGap(driver.gap_to_leader)}
          </span>
        </div>
      </div>

      {/* Body — telemetry gauges OR positional fallback */}
      <div className="p-3">
        {telemetryAvailable && telemetry ? (
          <>
            {/* Top row: Speed gauge | Gear + DRS | RPM gauge */}
            <div className="grid grid-cols-3 gap-3 mb-3">
              <CircularGauge
                value={telemetry.speed}
                maxValue={360}
                label="Speed"
                unit="km/h"
                color={teamColor}
                size={110}
              />
              <div className="flex flex-col items-center justify-center gap-2">
                <GearDisplay
                  gear={telemetry.n_gear}
                  rpm={telemetry.rpm}
                />
                <DRSIndicator drsCode={telemetry.drs} />
              </div>
              <CircularGauge
                value={telemetry.rpm}
                maxValue={15000}
                label="RPM"
                unit="RPM"
                color="var(--color-f1-red)"
                size={110}
              />
            </div>

            {/* Throttle + Brake bars */}
            <div className="grid grid-cols-2 gap-3 mb-3">
              <BarGauge value={telemetry.throttle} label="Throttle" color="bg-f1-green" />
              <BarGauge value={telemetry.brake} label="Brake" color="bg-f1-red" />
            </div>

            {/* Tyre + Interval + Stops row */}
            <div className="flex items-center justify-between border-t border-f1-border pt-2 mb-3">
              <TyreCompound compound={driver.current_compound} age={driver.tyre_age} />
              <div className="text-center">
                <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
                  Interval
                </div>
                <div className="text-sm font-mono text-f1-white tabular-nums">
                  {driver.interval === 0 ? "—" : `+${driver.interval.toFixed(3)}s`}
                </div>
              </div>
              <div className="text-center">
                <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
                  Stops
                </div>
                <div className="text-sm font-f1 font-bold text-f1-white">
                  {driver.stops_completed}
                </div>
              </div>
            </div>

            {/* Speed chart (small, below gauges) */}
            <div className="h-20 border-t border-f1-border pt-2">
              <TelemetryChart driverNumber={driver.driver_number} teamColor={teamColor} />
            </div>
          </>
        ) : (
          /* Positional-only fallback — similar to the old LiveDashboard */
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-f1-muted font-f1">Gap</span>
              <span className="font-mono text-f1-white tabular-nums">
                {formatGap(driver.gap_to_leader)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-f1-muted font-f1">Interval</span>
              <span className="font-mono text-f1-white tabular-nums">
                {driver.interval === 0 ? "—" : `+${driver.interval.toFixed(3)}s`}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-f1-muted font-f1">Tyre</span>
              <span className="flex items-center gap-2">
                <span
                  className="px-1.5 py-0.5 rounded text-xs font-bold"
                  style={{
                    backgroundColor: compoundColor(driver.current_compound),
                    color: driver.current_compound.toUpperCase() === "HARD" ? "#000" : "#fff",
                  }}
                >
                  {driver.current_compound}
                </span>
                <span className="font-mono text-f1-muted text-xs">{driver.tyre_age}L</span>
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-f1-muted font-f1">Stops</span>
              <span className="font-mono text-f1-white">{driver.stops_completed}</span>
            </div>
            {/* Stint history */}
            {driver.compounds_used.length > 0 && (
              <div className="flex justify-between text-sm">
                <span className="text-f1-muted font-f1">Stints</span>
                <span className="flex items-center gap-1">
                  {driver.compounds_used.map((c, i) => (
                    <span key={i} className="flex items-center gap-0.5">
                      {i > 0 && <span className="text-f1-muted text-xs">→</span>}
                      <span
                        className="w-4 h-4 rounded text-[10px] flex items-center justify-center font-bold"
                        style={{
                          backgroundColor: compoundColor(c),
                          color: c.toUpperCase() === "HARD" ? "#000" : "#fff",
                        }}
                      >
                        {c.charAt(0)}
                      </span>
                    </span>
                  ))}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Strategy recommendations (condensed) */}
        {strategies.length > 0 && (
          <div className="border-t border-f1-border mt-3 pt-2">
            <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1 mb-1.5">
              Strategy
            </div>
            {strategies.map((strat, i) => (
              <div key={i} className="flex items-center gap-2 mb-1 text-xs">
                <span className="text-f1-muted font-mono w-5">#{strat.rank}</span>
                <div className="flex items-center gap-0.5 flex-1">
                  {strat.stints.map((stint: Stint, j: number) => (
                    <span key={j} className="flex items-center gap-0.5">
                      {j > 0 && <span className="text-f1-muted">→</span>}
                      <span
                        className="px-1 rounded text-[10px] font-bold"
                        style={{
                          backgroundColor: compoundColor(stint.compound),
                          color: stint.compound.toUpperCase() === "HARD" ? "#000" : "#fff",
                        }}
                      >
                        {stint.compound.charAt(0)}
                      </span>
                    </span>
                  ))}
                </div>
                {strat.gap_to_best_s > 0 && (
                  <span className="text-f1-muted font-mono">+{strat.gap_to_best_s.toFixed(1)}s</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
