/**
 * DriverPanel — broadcast-style telemetry display for one driver.
 *
 * Layout matches the F1 TV broadcast aesthetic:
 *   Row 1: Driver number + abbreviation + name | Current lap time
 *   Row 2: Speed gauge | Gear + shift lights | Throttle + Brake bars
 *   Row 3: DRS indicator (centered)
 *   Row 4: RPM gauge | Tyre compound circle + age | Interval + Stops info
 *   Row 5: Speed-over-time chart
 *   Row 6: Strategy recommendations
 *
 * Falls back to a simpler positional-only view when telemetry is unavailable.
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
      {/* ── Header: driver number + name | current lap time ── */}
      <div
        className="px-4 py-2.5 flex items-start justify-between"
        style={{ borderBottom: `2px solid ${teamColor}` }}
      >
        <div className="flex items-baseline gap-2">
          {/* Large italic driver number in team colour */}
          <span
            className="text-3xl font-f1 font-bold italic leading-none"
            style={{ color: teamColor }}
          >
            {driver.driver_number}
          </span>
          <div className="flex flex-col">
            <span className="text-xl font-f1 font-bold text-f1-white leading-tight tracking-wide">
              {driver.abbreviation}
            </span>
            <span className="text-[10px] text-f1-muted font-f1 uppercase tracking-wider">
              {driver.full_name}
            </span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
            Current Lap
          </div>
          <div className="text-xl font-mono text-f1-white tabular-nums font-bold">
            {driver.last_lap_time ?? "—:——.———"}
          </div>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="p-3">
        {telemetryAvailable && telemetry ? (
          <>
            {/* ── Row 1: Speed | Gear+ShiftLights | Throttle+Brake ── */}
            <div className="grid grid-cols-[1fr_auto_1fr] gap-2 items-center mb-2">
              {/* Speed gauge — large, white arc */}
              <CircularGauge
                value={telemetry.speed}
                maxValue={360}
                label="Speed"
                unit="KM/H"
                color="#FFFFFF"
                size={130}
              />

              {/* Gear display with shift lights */}
              <div className="flex flex-col items-center px-2">
                <GearDisplay
                  gear={telemetry.n_gear}
                  rpm={telemetry.rpm}
                />
              </div>

              {/* Throttle + Brake stacked */}
              <div className="flex flex-col gap-3 justify-center">
                <BarGauge value={telemetry.throttle} label="Throttle" color="bg-f1-green" />
                <BarGauge value={telemetry.brake} label="Brake" color="bg-f1-red" />
              </div>
            </div>

            {/* ── DRS (centered) ── */}
            <div className="flex justify-center mb-2">
              <DRSIndicator drsCode={telemetry.drs} />
            </div>

            {/* ── Row 2: RPM | Tyre | Info boxes ── */}
            <div className="grid grid-cols-[1fr_auto_1fr] gap-2 items-center mb-2">
              {/* RPM gauge — red arc */}
              <CircularGauge
                value={telemetry.rpm}
                maxValue={15000}
                label="RPM"
                unit="REV"
                color="var(--color-f1-red)"
                size={130}
              />

              {/* Tyre compound circle + age */}
              <TyreCompound compound={driver.current_compound} age={driver.tyre_age} />

              {/* Info boxes: interval + stops */}
              <div className="flex flex-col gap-2">
                <div className="bg-f1-dark border border-f1-border rounded px-3 py-1.5">
                  <div className="text-[9px] uppercase tracking-wider text-f1-muted font-f1">
                    Interval
                  </div>
                  <div className="text-sm font-mono text-f1-white tabular-nums">
                    {driver.interval === 0 ? "Leader" : `+${driver.interval.toFixed(3)}s`}
                  </div>
                </div>
                <div className="bg-f1-dark border border-f1-border rounded px-3 py-1.5">
                  <div className="text-[9px] uppercase tracking-wider text-f1-muted font-f1">
                    Stops
                  </div>
                  <div className="text-sm font-f1 font-bold text-f1-white tabular-nums">
                    {driver.stops_completed}
                  </div>
                </div>
              </div>
            </div>

            {/* ── Speed chart ── */}
            <div className="h-20 border-t border-f1-border pt-2">
              <TelemetryChart driverNumber={driver.driver_number} teamColor={teamColor} />
            </div>
          </>
        ) : (
          /* ── Positional-only fallback (free tier) ── */
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
                      {i > 0 && <span className="text-f1-muted text-xs">&rarr;</span>}
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

        {/* ── Strategy recommendations ── */}
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
                      {j > 0 && <span className="text-f1-muted">&rarr;</span>}
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
