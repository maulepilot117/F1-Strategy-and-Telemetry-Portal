/**
 * TrackMap — SVG track visualization with circuit outline and timing sectors.
 *
 * When a track outline is available (fetched from one driver's location data
 * during replay setup), the component renders:
 *   1. Three colored sector polylines (red/blue/yellow) showing the circuit shape
 *   2. S1/S2/S3 labels at sector boundary points
 *   3. A start/finish marker at the first outline point
 *   4. Driver position dots on top
 *
 * The bounding box is computed from the outline points (the full circuit) rather
 * than from driver positions, so the view stays stable as drivers move around.
 *
 * Smooth animation is handled externally by useAnimatedTelemetry in the parent
 * component — the carData prop already contains 60fps-interpolated positions,
 * so this component just renders them directly.
 *
 * When no outline is available, falls back to the dots-only view where the
 * track shape reveals itself from driver positions over 2-3 polling cycles.
 */

import { useMemo } from "react";
import type { LiveDriver, TelemetryData, TrackPoint } from "../../types";

interface TrackMapProps {
  drivers: Record<number, LiveDriver>;
  /** Per-driver telemetry — animated at 60fps by useAnimatedTelemetry in the parent */
  carData: Record<number, TelemetryData>;
  /** Driver numbers of the selected team (highlighted) */
  selectedDrivers: number[];
  /** Circuit outline points from OpenF1 location data — null until fetched */
  trackOutline: TrackPoint[] | null;
}

// ---------------------------------------------------------------------------
// Track geometry helpers
// ---------------------------------------------------------------------------

/** Compute cumulative arc length along a polyline and find the points
 *  at 1/3 and 2/3 of total distance for sector boundaries. */
function computeSectorBoundaries(
  points: Array<{ nx: number; ny: number }>,
): { s1End: number; s2End: number } {
  if (points.length < 3) return { s1End: 0, s2End: 0 };

  // Build cumulative distance array
  const cumDist: number[] = [0];
  for (let i = 1; i < points.length; i++) {
    const dx = points[i].nx - points[i - 1].nx;
    const dy = points[i].ny - points[i - 1].ny;
    cumDist.push(cumDist[i - 1] + Math.sqrt(dx * dx + dy * dy));
  }
  const totalDist = cumDist[cumDist.length - 1];

  // Find indices at 1/3 and 2/3 of total distance
  const oneThird = totalDist / 3;
  const twoThirds = (totalDist * 2) / 3;

  let s1End = 0;
  let s2End = 0;
  for (let i = 0; i < cumDist.length; i++) {
    if (cumDist[i] >= oneThird && s1End === 0) s1End = i;
    if (cumDist[i] >= twoThirds && s2End === 0) s2End = i;
  }

  return { s1End, s2End };
}

/** Convert a list of normalized points into an SVG polyline points string */
function toPolylineStr(pts: Array<{ nx: number; ny: number }>): string {
  return pts.map((p) => `${p.nx},${p.ny}`).join(" ");
}

// Sector colors — F1 broadcast convention (red/blue/yellow)
const SECTOR_COLORS = {
  s1: "#e8364a", // Red
  s2: "#3b82f6", // Blue
  s3: "#eab308", // Yellow
} as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TrackMap({
  drivers,
  carData,
  selectedDrivers,
  trackOutline,
}: TrackMapProps) {
  // Extract driver positions from carData (already animated at 60fps by the parent)
  const positions = useMemo(() => {
    const pts: Array<{ num: number; x: number; y: number }> = [];
    for (const [numStr, td] of Object.entries(carData)) {
      if (td.x !== undefined && td.y !== undefined && td.x !== 0 && td.y !== 0) {
        pts.push({ num: Number(numStr), x: td.x, y: td.y });
      }
    }
    return pts;
  }, [carData]);

  // Compute bounding box — from outline if available (stable), otherwise from driver positions.
  // Using the outline gives a fixed view that doesn't jump as drivers move around the track.
  const bounds = useMemo(() => {
    const source = trackOutline && trackOutline.length > 0 ? trackOutline : positions;
    if (source.length === 0) return { minX: 0, maxX: 100, minY: 0, maxY: 100 };

    const xs = source.map((p) => p.x);
    const ys = source.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    // 5% padding so dots at the edges aren't clipped
    const padX = (maxX - minX) * 0.05 || 1;
    const padY = (maxY - minY) * 0.05 || 1;
    return { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
  }, [trackOutline, positions]);

  // Normalize a raw coordinate to 0-100 SVG space
  function norm(x: number, y: number): { nx: number; ny: number } {
    const rangeX = bounds.maxX - bounds.minX || 1;
    const rangeY = bounds.maxY - bounds.minY || 1;
    return {
      nx: ((x - bounds.minX) / rangeX) * 100,
      ny: ((y - bounds.minY) / rangeY) * 100,
    };
  }

  // Normalize all outline points and compute sector boundaries
  const outlineData = useMemo(() => {
    if (!trackOutline || trackOutline.length < 10) return null;

    const normalized = trackOutline.map((p) => norm(p.x, p.y));
    const { s1End, s2End } = computeSectorBoundaries(normalized);

    // Split into 3 sector segments.  Each sector's polyline includes the
    // boundary point of the next sector so the segments connect seamlessly.
    const s1Points = normalized.slice(0, s1End + 1);
    const s2Points = normalized.slice(s1End, s2End + 1);
    const s3Points = normalized.slice(s2End);

    return {
      s1Points,
      s2Points,
      s3Points,
      // Sector boundary positions for labels
      startFinish: normalized[0],
      s1Boundary: normalized[s1End],
      s2Boundary: normalized[s2End],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- bounds is stable when outline is set
  }, [trackOutline, bounds]);

  const selectedSet = new Set(selectedDrivers);

  if (positions.length === 0 && !trackOutline) {
    return (
      <div className="h-full flex items-center justify-center text-f1-muted text-xs font-f1">
        Waiting for position data...
      </div>
    );
  }

  return (
    <svg viewBox="0 0 100 100" className="w-full h-full" preserveAspectRatio="xMidYMid meet">
      {/* Layer 1: Track outline sectors (bottom) */}
      {outlineData && (
        <g>
          {/* S1 — Red */}
          <polyline
            points={toPolylineStr(outlineData.s1Points)}
            fill="none"
            stroke={SECTOR_COLORS.s1}
            strokeWidth="1.2"
            opacity={0.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* S2 — Blue */}
          <polyline
            points={toPolylineStr(outlineData.s2Points)}
            fill="none"
            stroke={SECTOR_COLORS.s2}
            strokeWidth="1.2"
            opacity={0.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* S3 — Yellow */}
          <polyline
            points={toPolylineStr(outlineData.s3Points)}
            fill="none"
            stroke={SECTOR_COLORS.s3}
            strokeWidth="1.2"
            opacity={0.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      )}

      {/* Layer 2: Sector boundary labels */}
      {outlineData && (
        <g>
          {/* Start/finish marker */}
          <circle
            cx={outlineData.startFinish.nx}
            cy={outlineData.startFinish.ny}
            r={1.5}
            fill="white"
            opacity={0.8}
          />
          <text
            x={outlineData.startFinish.nx}
            y={outlineData.startFinish.ny - 3}
            textAnchor="middle"
            fill="white"
            fontSize={2.8}
            fontFamily="var(--font-f1)"
            fontWeight="700"
            opacity={0.7}
          >
            S/F
          </text>

          {/* S1/S2 boundary */}
          <circle
            cx={outlineData.s1Boundary.nx}
            cy={outlineData.s1Boundary.ny}
            r={1}
            fill="white"
            opacity={0.6}
          />
          <text
            x={outlineData.s1Boundary.nx}
            y={outlineData.s1Boundary.ny - 3}
            textAnchor="middle"
            fill={SECTOR_COLORS.s1}
            fontSize={2.5}
            fontFamily="var(--font-f1)"
            fontWeight="600"
            opacity={0.8}
          >
            S1
          </text>

          {/* S2/S3 boundary */}
          <circle
            cx={outlineData.s2Boundary.nx}
            cy={outlineData.s2Boundary.ny}
            r={1}
            fill="white"
            opacity={0.6}
          />
          <text
            x={outlineData.s2Boundary.nx}
            y={outlineData.s2Boundary.ny - 3}
            textAnchor="middle"
            fill={SECTOR_COLORS.s2}
            fontSize={2.5}
            fontFamily="var(--font-f1)"
            fontWeight="600"
            opacity={0.8}
          >
            S2
          </text>
        </g>
      )}

      {/* Layer 3: Non-selected driver dots */}
      {positions
        .filter(({ num }) => !selectedSet.has(num))
        .map(({ num, x, y }) => {
          const { nx, ny } = norm(x, y);
          const driver = drivers[num];
          const color = driver?.team_color ?? "#666";
          return (
            <circle
              key={num}
              cx={nx}
              cy={ny}
              r={1.2}
              fill={color}
              opacity={0.4}
            />
          );
        })}

      {/* Layer 4: Selected team drivers (on top, with labels) */}
      {positions
        .filter(({ num }) => selectedSet.has(num))
        .map(({ num, x, y }) => {
          const { nx, ny } = norm(x, y);
          const driver = drivers[num];
          const color = driver?.team_color ?? "#888";
          return (
            <g key={num}>
              <circle cx={nx} cy={ny} r={2.5} fill={color} opacity={1} />
              {driver && (
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
