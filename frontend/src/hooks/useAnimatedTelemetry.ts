/**
 * useAnimatedTelemetry — 60fps animation through telemetry + location buffers.
 *
 * The backend sends one SSE message per ~4-second cycle with two buffers:
 *   - car_data_buffer: chronological speed/RPM/throttle/brake/gear/DRS records
 *   - location_buffer: chronological x/y position records
 *
 * Two techniques prevent visible jumps:
 *
 * 1. Linear interpolation (lerp) between adjacent buffer samples.
 *    Without this, the animation snaps between ~15 discrete positions over 3.5s
 *    (a visible jump every ~230ms). With lerp, values glide smoothly.
 *
 * 2. Bridge from previous frame. When a new buffer arrives, the first
 *    interpolation segment transitions from where the last animation ended
 *    to the new buffer's first sample. Without this, there's a visible jump
 *    at every buffer boundary (every ~4 seconds).
 *
 * Returns a Record<number, TelemetryData> — same shape as raceState.car_data —
 * so it drops in as a replacement wherever telemetry is consumed.
 *
 * When no buffers are available (live mode, or before first replay data),
 * returns the static car_data directly (no animation).
 */

import { useState, useEffect, useRef } from "react";
import type {
  TelemetryData,
  CarDataBufferEntry,
  LocationBufferEntry,
} from "../types";

/** Duration to animate through one buffer — slightly under the 4s cycle
 *  so the animation completes just before the next buffer arrives. */
const ANIMATION_DURATION_MS = 3500;

/** Empty TelemetryData for drivers that appear in buffers but not in static data */
const EMPTY_TELEMETRY: TelemetryData = {
  speed: 0, rpm: 0, n_gear: 0, throttle: 0, brake: 0, drs: 0, x: 0, y: 0,
};

/** Simple linear interpolation: a + (b - a) * t */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Animate through car_data + location buffers at 60fps.
 *
 * Groups both buffers by driver, then on each animation frame linearly
 * interpolates between adjacent samples at the current progress point
 * (0→1 over ANIMATION_DURATION_MS). Merges telemetry fields from car_data
 * and position fields from location into a single TelemetryData per driver.
 *
 * When a new buffer arrives, a "bridge" segment smoothly transitions from
 * the last rendered position to the first sample of the new buffer,
 * eliminating the jump at buffer boundaries.
 *
 * Falls back to staticCarData when buffers are empty (live mode).
 */
export function useAnimatedTelemetry(
  carDataBuffer: CarDataBufferEntry[],
  locationBuffer: LocationBufferEntry[],
  staticCarData: Record<number, TelemetryData>,
): Record<number, TelemetryData> {
  const [animated, setAnimated] = useState<Record<number, TelemetryData>>(staticCarData);
  const animRef = useRef<number>(0);
  // Cheap identity to detect when new buffers arrive — avoids restarting
  // animation on every React render when the buffer reference is the same data.
  const prevIdRef = useRef<string>("");
  // Last rendered frame — used to bridge smoothly into the next buffer.
  // Without this, the animation jumps from the last sample of buffer N
  // to the first sample of buffer N+1 every ~4 seconds.
  const lastFrameRef = useRef<Record<number, TelemetryData>>({});

  useEffect(() => {
    const hasData = carDataBuffer.length > 0 || locationBuffer.length > 0;
    if (!hasData) {
      // No buffers (live mode or no data yet) — use static data directly
      setAnimated(staticCarData);
      return;
    }

    // Build identity from buffer sizes + first entries
    const carFirst = carDataBuffer[0];
    const locFirst = locationBuffer[0];
    const id = `${carDataBuffer.length}:${carFirst?.dn},${carFirst?.s}|${locationBuffer.length}:${locFirst?.dn},${locFirst?.x}`;
    if (id === prevIdRef.current) return;
    prevIdRef.current = id;

    cancelAnimationFrame(animRef.current);

    // Group car_data records by driver (chronological order preserved)
    const carByDriver = new Map<number, CarDataBufferEntry[]>();
    for (const entry of carDataBuffer) {
      let arr = carByDriver.get(entry.dn);
      if (!arr) { arr = []; carByDriver.set(entry.dn, arr); }
      arr.push(entry);
    }

    // Group location records by driver
    const locByDriver = new Map<number, LocationBufferEntry[]>();
    for (const entry of locationBuffer) {
      let arr = locByDriver.get(entry.dn);
      if (!arr) { arr = []; locByDriver.set(entry.dn, arr); }
      arr.push(entry);
    }

    // Snapshot last rendered frame for bridge interpolation.
    // This is captured once when the new buffer arrives, not on every frame.
    const prevFrame = lastFrameRef.current;
    const startTime = performance.now();

    function animate(now: number) {
      const progress = Math.min(1, (now - startTime) / ANIMATION_DURATION_MS);
      const result: Record<number, TelemetryData> = {};

      // Merge all driver numbers from both buffers
      const allDrivers = new Set([...carByDriver.keys(), ...locByDriver.keys()]);

      for (const driverNum of allDrivers) {
        const base = staticCarData[driverNum] ?? EMPTY_TELEMETRY;
        const prev = prevFrame[driverNum];
        // Only bridge if this driver had meaningful data in the previous frame.
        // Without this check, first-time drivers would lerp from (0,0).
        const useBridge = prev != null && (prev.x !== 0 || prev.y !== 0 || prev.speed !== 0);

        let speed = base.speed, rpm = base.rpm, n_gear = base.n_gear;
        let throttle = base.throttle, brake = base.brake, drs = base.drs;
        let x = base.x, y = base.y;

        // --- Car data: lerp between adjacent samples ---
        // With bridge, effective point list is [prev, sample0, sample1, ...sampleN]
        // giving N+1 points and N segments to interpolate through.
        // Without bridge, it's [sample0, sample1, ...sampleN] with N-1 segments.
        const carPts = carByDriver.get(driverNum);
        if (carPts && carPts.length > 0) {
          const pointCount = carPts.length + (useBridge ? 1 : 0);
          const segments = pointCount - 1;

          if (segments > 0) {
            const t = progress * segments;
            const seg = Math.min(Math.floor(t), segments - 1);
            const frac = t - seg;

            // Accessors for effective point list — index 0 is the bridge
            // point (prev frame values) when bridging is active.
            const getS = (i: number) => useBridge ? (i === 0 ? prev!.speed : carPts[i - 1].s) : carPts[i].s;
            const getR = (i: number) => useBridge ? (i === 0 ? prev!.rpm : carPts[i - 1].r) : carPts[i].r;
            const getT = (i: number) => useBridge ? (i === 0 ? prev!.throttle : carPts[i - 1].t) : carPts[i].t;
            const getG = (i: number) => useBridge ? (i === 0 ? prev!.n_gear : carPts[i - 1].g) : carPts[i].g;
            const getB = (i: number) => useBridge ? (i === 0 ? prev!.brake : carPts[i - 1].b) : carPts[i].b;
            const getD = (i: number) => useBridge ? (i === 0 ? prev!.drs : carPts[i - 1].d) : carPts[i].d;

            // Continuous values — smooth linear interpolation
            speed = lerp(getS(seg), getS(seg + 1), frac);
            rpm = lerp(getR(seg), getR(seg + 1), frac);
            throttle = lerp(getT(seg), getT(seg + 1), frac);
            // Discrete values — snap at midpoint (gear, brake, DRS are integers)
            n_gear = frac < 0.5 ? getG(seg) : getG(seg + 1);
            brake = frac < 0.5 ? getB(seg) : getB(seg + 1);
            drs = frac < 0.5 ? getD(seg) : getD(seg + 1);
          } else {
            // Single sample, no bridge — snap directly
            speed = carPts[0].s; rpm = carPts[0].r; n_gear = carPts[0].g;
            throttle = carPts[0].t; brake = carPts[0].b; drs = carPts[0].d;
          }
        }

        // --- Location: lerp between adjacent samples ---
        const locPts = locByDriver.get(driverNum);
        if (locPts && locPts.length > 0) {
          const pointCount = locPts.length + (useBridge ? 1 : 0);
          const segments = pointCount - 1;

          if (segments > 0) {
            const t = progress * segments;
            const seg = Math.min(Math.floor(t), segments - 1);
            const frac = t - seg;

            const getX = (i: number) => useBridge ? (i === 0 ? prev!.x : locPts[i - 1].x) : locPts[i].x;
            const getY = (i: number) => useBridge ? (i === 0 ? prev!.y : locPts[i - 1].y) : locPts[i].y;

            x = lerp(getX(seg), getX(seg + 1), frac);
            y = lerp(getY(seg), getY(seg + 1), frac);
          } else {
            x = locPts[0].x; y = locPts[0].y;
          }
        }

        result[driverNum] = { speed, rpm, n_gear, throttle, brake, drs, x, y };
      }

      lastFrameRef.current = result;
      setAnimated(result);

      if (progress < 1) {
        animRef.current = requestAnimationFrame(animate);
      }
    }

    animRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animRef.current);
  }, [carDataBuffer, locationBuffer, staticCarData]);

  return animated;
}
