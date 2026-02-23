/**
 * LapDelta — gap badge and lap counter between two team drivers.
 *
 * Displayed as a vertical column between the driver panels.
 * The gap value is shown in a prominent badge (red when behind, green when close).
 * Lap counter shows current/total below.
 */

import type { LiveDriver } from "../../types";

interface LapDeltaProps {
  driver1: LiveDriver | undefined;
  driver2: LiveDriver | undefined;
  currentLap: number;
  totalLaps: number;
}

export default function LapDelta({ driver1, driver2, currentLap, totalLaps }: LapDeltaProps) {
  // Calculate gap between the two team drivers
  let deltaText = "—";
  let hasGap = false;
  if (driver1 && driver2) {
    const gap = Math.abs(driver1.gap_to_leader - driver2.gap_to_leader);
    if (gap > 0) {
      deltaText = `+${gap.toFixed(3)}`;
      hasGap = true;
    }
  }

  return (
    <div className="flex flex-col items-center justify-center gap-4 px-3 min-w-[80px]">
      {/* Gap badge — prominent display between drivers */}
      <div className="text-center">
        <div className="text-[9px] uppercase tracking-wider text-f1-muted font-f1 mb-1">
          Gap
        </div>
        <div
          className={`px-3 py-1.5 rounded border font-mono tabular-nums text-base font-bold ${
            hasGap
              ? "bg-f1-red/20 border-f1-red/50 text-f1-red"
              : "bg-f1-dark border-f1-border text-f1-muted"
          }`}
        >
          {deltaText}
        </div>
      </div>

      {/* Vertical divider */}
      <div className="w-px h-6 bg-f1-border" />

      {/* Lap counter */}
      <div className="text-center">
        <div className="text-[9px] uppercase tracking-wider text-f1-muted font-f1 mb-1">
          Lap
        </div>
        <div className="bg-f1-dark border border-f1-border rounded px-3 py-1.5">
          <span className="text-xl font-f1 font-bold text-f1-white tabular-nums">
            {currentLap}
          </span>
          <span className="text-f1-muted text-sm font-f1">
            /{totalLaps || "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
