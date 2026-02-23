/**
 * SSE hook for live race data using useSyncExternalStore.
 *
 * This hook connects to the backend's Server-Sent Events endpoint and
 * keeps a module-level state object in sync with the latest race state.
 * React components subscribe via useSyncExternalStore, which prevents
 * tearing during concurrent rendering (unlike manual useState+useEffect).
 *
 * The pattern:
 *   EventSource.onmessage → update module state → notify listeners → React re-renders
 *
 * Every SSE message is a full state snapshot (no partial updates), so:
 *   - Reconnection is free — the next message has everything
 *   - No event merging or reconciliation needed
 *   - The server sends keepalive comments every 15s during quiet periods
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { LiveRaceState, TelemetryData } from "../types";

// ---------------------------------------------------------------------------
// Module-level state store — shared across all components using this hook
// ---------------------------------------------------------------------------

/** Initial empty state before any SSE data arrives */
const INITIAL_STATE: LiveRaceState = {
  session_key: null,
  current_lap: 0,
  total_laps: 0,
  is_safety_car: false,
  last_race_control_message: "",
  drivers: {},
  race_control_log: [],
  pit_log: [],
  strategies: {},
  last_updated: null,
  connected_to_openf1: false,
  polling_active: false,
  car_data: {},
  telemetry_available: false,
  replay_mode: false,
  replay_speed: 1,
  replay_elapsed_pct: 0,
  connected: false,
  lastUpdate: 0,
};

// ---------------------------------------------------------------------------
// Telemetry history — accumulated client-side from SSE snapshots
// ---------------------------------------------------------------------------

/** Max number of telemetry samples to keep per driver (at ~4-8s polling,
 *  60 samples = ~4-8 minutes of chart data) */
const MAX_HISTORY = 60;

/** Per-driver ring buffer of telemetry snapshots with timestamps.
 *  Accumulated in onmessage — resets when the SSE connection resets. */
const telemetryHistory: Map<number, Array<TelemetryData & { ts: number }>> = new Map();

let state: LiveRaceState = { ...INITIAL_STATE };

/** All components currently subscribed to state changes */
const listeners = new Set<() => void>();

/** Notify all subscribed components that state has changed */
function emitChange() {
  listeners.forEach((l) => l());
}

// ---------------------------------------------------------------------------
// EventSource connection management
// ---------------------------------------------------------------------------

/** The active EventSource connection (null when disconnected) */
let eventSource: EventSource | null = null;

/**
 * Connect to the SSE endpoint for a race session.
 *
 * Returns a cleanup function that closes the connection —
 * designed to be returned from useEffect.
 */
function connectToRace(sessionKey: number): () => void {
  // Close any existing connection first
  if (eventSource) {
    eventSource.close();
  }

  const apiBase = import.meta.env.VITE_API_BASE ?? "";
  const es = new EventSource(`${apiBase}/api/live/stream/${sessionKey}`);

  es.onmessage = (e) => {
    // Each message is a full state snapshot from the backend.
    // We spread the parsed data and add our frontend-only fields.
    const data = JSON.parse(e.data);
    state = {
      ...data,
      connected: true,
      lastUpdate: Date.now(),
    };

    // Accumulate telemetry history for charts — one snapshot per SSE message.
    // Each driver's car_data is pushed into a ring buffer (max MAX_HISTORY).
    const carData = data.car_data as Record<number, TelemetryData> | undefined;
    if (carData) {
      const now = Date.now();
      for (const [driverStr, td] of Object.entries(carData)) {
        const driverNum = Number(driverStr);
        let hist = telemetryHistory.get(driverNum);
        if (!hist) {
          hist = [];
          telemetryHistory.set(driverNum, hist);
        }
        hist.push({ ...td, ts: now });
        // Trim to ring buffer size
        if (hist.length > MAX_HISTORY) {
          hist.splice(0, hist.length - MAX_HISTORY);
        }
      }
    }

    emitChange();
  };

  es.onerror = () => {
    // EventSource auto-reconnects on error — just update the connected flag.
    // The next successful message will set connected back to true.
    state = { ...state, connected: false };
    emitChange();
  };

  es.onopen = () => {
    state = { ...state, connected: true };
    emitChange();
  };

  eventSource = es;

  // Return cleanup function for useEffect
  return () => {
    es.close();
    eventSource = null;
    state = { ...INITIAL_STATE };
    telemetryHistory.clear();
    emitChange();
  };
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

/**
 * Subscribe to live race data via SSE.
 *
 * Pass a session key to connect, or null to disconnect.
 * Returns the current LiveRaceState — components re-render when it changes.
 *
 * Usage:
 *   const raceState = useLiveRace(sessionKey);
 *   // raceState.drivers, raceState.current_lap, etc.
 */
export function useLiveRace(sessionKey: number | null): LiveRaceState {
  // Connect/disconnect when sessionKey changes
  useEffect(() => {
    if (!sessionKey) return;
    return connectToRace(sessionKey);
  }, [sessionKey]);

  // Subscribe to state changes via useSyncExternalStore.
  // This is the recommended React 19 pattern for external stores —
  // it prevents "tearing" where different parts of the UI show
  // different versions of the same data during concurrent rendering.
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}

/**
 * Get the telemetry history for a specific driver.
 *
 * Returns up to MAX_HISTORY snapshots accumulated from SSE messages.
 * Re-renders whenever the SSE state changes (which means new data arrived).
 * History resets on page refresh — acceptable for a real-time dashboard.
 */
export function useTelemetryHistory(
  driverNumber: number | null,
): Array<TelemetryData & { ts: number }> {
  // Subscribe to the same external store so we re-render on new SSE data.
  // We read from the module-level telemetryHistory map (populated in onmessage).
  const subscribe = useCallback((callback: () => void) => {
    listeners.add(callback);
    return () => listeners.delete(callback);
  }, []);

  const getSnapshot = useCallback(() => {
    if (!driverNumber) return [];
    return telemetryHistory.get(driverNumber) ?? [];
  }, [driverNumber]);

  const history = useSyncExternalStore(subscribe, getSnapshot);

  // Memoize a copy so downstream components get a stable reference
  // when no new data has arrived.
  return useMemo(() => [...history], [history]);
}
