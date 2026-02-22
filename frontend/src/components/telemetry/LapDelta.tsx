/**
 * LapDelta — shows the gap between two team drivers.
 *
 * Displayed as a vertical divider between the two driver panels.
 * Shows interval difference and current lap counter.
 */

import type { LiveDriver } from "../../types";

interface LapDeltaProps {
  driver1: LiveDriver | undefined;
  driver2: LiveDriver | undefined;
  currentLap: number;
  totalLaps: number;
}

export default function LapDelta({ driver1, driver2, currentLap, totalLaps }: LapDeltaProps) {
  // Calculate gap between the two drivers
  let deltaText = "—";
  if (driver1 && driver2) {
    const gap = Math.abs(driver1.gap_to_leader - driver2.gap_to_leader);
    if (gap > 0) {
      deltaText = `${gap.toFixed(3)}s`;
    }
  }

  return (
    <div className="flex flex-col items-center justify-center gap-3 px-4">
      {/* Gap between team drivers */}
      <div className="text-center">
        <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
          Gap
        </div>
        <div className="text-lg font-mono text-f1-white tabular-nums">
          {deltaText}
        </div>
      </div>

      {/* Vertical divider line */}
      <div className="w-px h-8 bg-f1-border" />

      {/* Lap counter */}
      <div className="text-center">
        <div className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
          Lap
        </div>
        <div className="text-lg font-f1 font-bold text-f1-white tabular-nums">
          {currentLap}
          <span className="text-f1-muted text-sm">/{totalLaps || "—"}</span>
        </div>
      </div>
    </div>
  );
}
