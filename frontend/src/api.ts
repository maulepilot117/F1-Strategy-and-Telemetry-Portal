/* Fetch wrappers for the backend API endpoints.
   Each function calls the FastAPI backend and returns typed data.
   The base URL points to the local dev server (port 8000). */

import type {
  ScheduleEvent,
  DegradationResponse,
  StrategyResponse,
  StrategyRequest,
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
