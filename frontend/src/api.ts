/* Fetch wrappers for the backend API endpoints.
   Each function calls the FastAPI backend and returns typed data.
   The base URL points to the local dev server (port 8000). */

import type {
  ScheduleEvent,
  DegradationResponse,
  StrategyResponse,
  StrategyRequest,
  LiveStatusResponse,
  LiveDriversResponse,
} from "./types";

// In Docker, the empty string means "same host that served the page" (nginx on port 80).
// For local dev without Docker, VITE_API_BASE is set in .env.development to http://localhost:8000.
const BASE = import.meta.env.VITE_API_BASE ?? "";

/** Fetch the race schedule for a season (populates the GP dropdown). */
export async function fetchSchedule(year: number): Promise<ScheduleEvent[]> {
  const res = await fetch(`${BASE}/api/schedule/${year}`);
  if (!res.ok) throw new Error(`Schedule fetch failed: ${res.statusText}`);
  return res.json();
}

/** Fetch tyre degradation data for a specific Grand Prix. */
export async function fetchDegradation(
  year: number,
  grandPrix: string,
): Promise<DegradationResponse> {
  // encodeURIComponent handles GP names with spaces (e.g., "Abu Dhabi")
  const res = await fetch(
    `${BASE}/api/degradation/${year}/${encodeURIComponent(grandPrix)}`,
  );
  if (!res.ok) throw new Error(`Degradation fetch failed: ${res.statusText}`);
  return res.json();
}

/** Fetch ranked pit stop strategies for a race (dry conditions only). */
export async function fetchStrategy(
  year: number,
  grandPrix: string,
  raceLaps: number,
  pitStopLoss: number,
): Promise<StrategyResponse> {
  const params = new URLSearchParams({
    race_laps: raceLaps.toString(),
    pit_stop_loss: pitStopLoss.toString(),
  });
  const res = await fetch(
    `${BASE}/api/strategy/${year}/${encodeURIComponent(grandPrix)}?${params}`,
  );
  if (!res.ok) throw new Error(`Strategy fetch failed: ${res.statusText}`);
  return res.json();
}

/** Fetch strategies with optional mixed-weather conditions (POST endpoint).
 *
 * When weather_windows is provided in the request body, the backend simulates
 * a race with changing weather — forcing pit stops at weather transitions and
 * using the correct compounds (dry slicks vs wet tyres) per condition.
 *
 * When weather_windows is omitted, behaves identically to the GET endpoint.
 */
export async function fetchWeatherStrategy(
  year: number,
  grandPrix: string,
  request: StrategyRequest,
): Promise<StrategyResponse> {
  const res = await fetch(
    `${BASE}/api/strategy/${year}/${encodeURIComponent(grandPrix)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) throw new Error(`Weather strategy fetch failed: ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Live race tracking endpoints
// ---------------------------------------------------------------------------

/** Check if a race session is available for live tracking.
 *
 * Resolves the year + GP to an OpenF1 session_key and returns
 * whether polling is already active.
 */
export async function fetchLiveStatus(
  year: number,
  grandPrix: string,
  sessionType: string = "Race",
): Promise<LiveStatusResponse> {
  const params = new URLSearchParams({ session_type: sessionType });
  const res = await fetch(
    `${BASE}/api/live/status/${year}/${encodeURIComponent(grandPrix)}?${params}`,
  );
  if (!res.ok) throw new Error(`Live status fetch failed: ${res.statusText}`);
  return res.json();
}

/** Get teams and drivers for the team selector dropdown. */
export async function fetchLiveDrivers(
  year: number,
  grandPrix: string,
): Promise<LiveDriversResponse> {
  const res = await fetch(
    `${BASE}/api/live/drivers/${year}/${encodeURIComponent(grandPrix)}`,
  );
  if (!res.ok) throw new Error(`Live drivers fetch failed: ${res.statusText}`);
  return res.json();
}

/** Start live race tracking (polling OpenF1) for a session.
 *
 * Idempotent — if already polling the same session, returns current status.
 * Passes year and grandPrix so the backend can recalculate strategies
 * mid-race using practice degradation data for the circuit.
 */
export async function startLiveTracking(
  sessionKey: number,
  totalLaps: number,
  year?: number,
  grandPrix?: string,
): Promise<{ status: string; session_key: number; total_laps: number; drivers: number }> {
  const params = new URLSearchParams({ total_laps: totalLaps.toString() });
  if (year !== undefined) params.set("year", year.toString());
  if (grandPrix !== undefined) params.set("grand_prix", grandPrix);
  const res = await fetch(
    `${BASE}/api/live/start/${sessionKey}?${params}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Start tracking failed: ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Replay endpoints
// ---------------------------------------------------------------------------

/** Start replaying a historical race session.
 *
 * Pre-fetches all data and plays it back through the same SSE stream.
 * Connect to `/api/live/stream/{sessionKey}` to receive replay data.
 */
export async function startReplay(
  sessionKey: number,
  totalLaps: number,
  year: number,
  grandPrix: string,
  speed: number = 4,
): Promise<{ status: string; session_key: number; speed: number }> {
  const params = new URLSearchParams({
    total_laps: totalLaps.toString(),
    year: year.toString(),
    grand_prix: grandPrix,
    speed: speed.toString(),
  });
  const res = await fetch(
    `${BASE}/api/replay/start/${sessionKey}?${params}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Start replay failed: ${res.statusText}`);
  return res.json();
}

/** Change replay playback speed (0=paused, 1/2/4/8). */
export async function setReplaySpeed(
  speed: number,
): Promise<{ speed: number }> {
  const res = await fetch(`${BASE}/api/replay/speed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speed }),
  });
  if (!res.ok) throw new Error(`Set replay speed failed: ${res.statusText}`);
  return res.json();
}

/** Stop the current replay and reset state. */
export async function stopReplay(): Promise<{ status: string }> {
  const res = await fetch(`${BASE}/api/replay/stop`, { method: "POST" });
  if (!res.ok) throw new Error(`Stop replay failed: ${res.statusText}`);
  return res.json();
}
