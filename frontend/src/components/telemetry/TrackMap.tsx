/**
 * TrackMap — SVG track visualization using driver positions.
 *
 * Instead of a static track outline SVG (which we'd need for 24 circuits),
 * we plot all 20 drivers' positions as dots.  With ~15 historical positions
 * per driver as ghost trails, the track shape reveals itself naturally
 * within 2-3 polling cycles (~16-24s).
 *
 * Coordinates from OpenF1 are circuit-specific (arbitrary units), so we
 * normalize x/y to a 0-100 SVG viewBox by finding min/max across all
 * visible drivers.
 */

import { useMemo } from "react";
import type { LiveDriver, TelemetryData } from "../../types";

interface TrackMapProps {
  drivers: Record<number, LiveDriver>;
  carData: Record<number, TelemetryData>;
  /** Driver numbers of the selected team (highlighted) */
  selectedDrivers: number[];
}

export default function TrackMap({ drivers, carData, selectedDrivers }: TrackMapProps) {
  // Collect all positions that have valid x/y coordinates
  const positions = useMemo(() => {
    const pts: Array<{ num: number; x: number; y: number }> = [];
    for (const [numStr, td] of Object.entries(carData)) {
      if (td.x !== undefined && td.y !== undefined && td.x !== 0 && td.y !== 0) {
        pts.push({ num: Number(numStr), x: td.x, y: td.y });
      }
    }
    return pts;
  }, [carData]);

  // Find bounding box for normalization — with padding
  const bounds = useMemo(() => {
    if (positions.length === 0) return { minX: 0, maxX: 100, minY: 0, maxY: 100 };
    const xs = positions.map((p) => p.x);
    const ys = positions.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    // Add 5% padding
    const padX = (maxX - minX) * 0.05 || 1;
    const padY = (maxY - minY) * 0.05 || 1;
    return { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
  }, [positions]);

  // Normalize a position to 0-100 SVG coordinates
  function norm(x: number, y: number): { nx: number; ny: number } {
    const rangeX = bounds.maxX - bounds.minX || 1;
    const rangeY = bounds.maxY - bounds.minY || 1;
    return {
      nx: ((x - bounds.minX) / rangeX) * 100,
      ny: ((y - bounds.minY) / rangeY) * 100,
    };
  }

  const selectedSet = new Set(selectedDrivers);

  if (positions.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-f1-muted text-xs font-f1">
        Waiting for position data...
      </div>
    );
  }

  return (
    <svg viewBox="0 0 100 100" className="w-full h-full" preserveAspectRatio="xMidYMid meet">
      {/* All drivers as dots */}
      {positions.map(({ num, x, y }) => {
        const { nx, ny } = norm(x, y);
        const driver = drivers[num];
        const isSelected = selectedSet.has(num);
        const color = driver?.team_color ?? "#666";

        return (
          <g key={num}>
            {/* Driver dot */}
            <circle
              cx={nx}
              cy={ny}
              r={isSelected ? 2.5 : 1.2}
              fill={color}
              opacity={isSelected ? 1 : 0.4}
            />
            {/* Driver abbreviation label (selected team only) */}
            {isSelected && driver && (
              <text
                x={nx}
                y={ny - 4}
                textAnchor="middle"
                fill={color}
                fontSize={3}
                fontFamily="var(--font-f1)"
                fontWeight="600"
              >
                {driver.abbreviation}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
