/* TypeScript interfaces matching the backend API response shapes.
   Keeping these in one file makes it easy to update if the API changes. */

/** A single event from GET /api/schedule/{year} */
export interface ScheduleEvent {
  round_number: number;
  event_name: string;
  country: string;
  location: string;
  date: string;
  event_format: string;
  /** Exact race start time (ISO string) — used for auto-detection and countdown */
  race_date_utc: string | null;
}

/** A single point on a degradation curve */
export interface DegradationPoint {
  tyre_age: number;
  avg_delta_s: number;
  sample_count: number;
}

/** Degradation info for one tyre compound */
export interface CompoundDegradation {
  degradation_per_lap_s: number;
  curve: DegradationPoint[];
}

/** Full response from GET /api/degradation/{year}/{grand_prix} */
export interface DegradationResponse {
  event_name: string;
  year: number;
  fuel_correction_s_per_lap: number;
  compounds: Record<string, CompoundDegradation>;
  /** Official race distance — auto-populated from race data when available */
  race_laps?: number;
  /** Median pit stop time loss in seconds — computed from actual pit stops */
  avg_pit_stop_loss_s?: number;
}

/** A weather window: a range of laps under one weather condition */
export interface WeatherWindow {
  start_lap: number;
  end_lap: number;
  condition: "dry" | "intermediate" | "wet";
}

/** A single stint within a strategy */
export interface Stint {
  compound: string;
  start_lap: number;
  end_lap: number;
  laps: number;
  /** Present in mixed-weather strategies — which condition this stint ran in */
  condition?: "dry" | "intermediate" | "wet";
}

/** A single ranked strategy */
export interface Strategy {
  name: string;
  total_time_s: number;
  num_stops: number;
  stints: Stint[];
  rank: number;
  gap_to_best_s: number;
}

/** FIA tyre regulations applied to this race */
export interface TyreRegulations {
  min_compounds: number;
  min_stops: number;
  max_stint_laps: number | null;
}

/** Full response from GET /api/strategy/{year}/{grand_prix} */
export interface StrategyResponse {
  event_name: string;
  year: number;
  race_laps: number;
  pit_stop_loss_s: number;
  base_lap_time_s: number | null;
  regulations: TyreRegulations;
  conditions: string;
  deg_rates_used: Record<string, number>;
  strategies: Strategy[];
  /** Present only in mixed-weather responses */
  weather_windows?: WeatherWindow[];
}

/** POST request body for the mixed-weather strategy endpoint */
export interface StrategyRequest {
  race_laps: number;
  pit_stop_loss?: number;
  fuel_correction?: number;
  intermediate_deg_rate?: number;
  wet_deg_rate?: number;
  weather_windows?: WeatherWindow[];
}

// ---------------------------------------------------------------------------
// Track outline types (circuit shape from OpenF1 location data)
// ---------------------------------------------------------------------------

/** A single point on the track outline, from one driver's location data */
export interface TrackPoint {
  x: number;
  y: number;
}

/** A single location record in the animation buffer (short keys to minimize SSE payload) */
export interface LocationBufferEntry {
  dn: number;  // driver_number
  x: number;
  y: number;
}

/** A single car_data record in the animation buffer (short keys to minimize SSE payload) */
export interface CarDataBufferEntry {
  dn: number;  // driver_number
  s: number;   // speed (km/h)
  r: number;   // rpm
  g: number;   // n_gear
  t: number;   // throttle (0-100)
  b: number;   // brake (0 or 100)
  d: number;   // drs code
}

// ---------------------------------------------------------------------------
// Telemetry types (car_data + location from OpenF1)
// ---------------------------------------------------------------------------

/** Real-time telemetry snapshot for a single driver.
 *  Populated from OpenF1 car_data (speed, rpm, gear, throttle, brake, drs)
 *  and location (x, y) endpoints. */
export interface TelemetryData {
  speed: number;     // km/h (0–360+)
  rpm: number;       // engine RPM (0–15000)
  n_gear: number;    // current gear (0–8, 0 = neutral)
  throttle: number;  // throttle position (0–100)
  brake: number;     // brake status (0 or 100 — OpenF1 only sends binary)
  drs: number;       // DRS code (see DRS_* sets below)
  x: number;         // track position X (circuit-specific units)
  y: number;         // track position Y (circuit-specific units)
}

/** DRS integer codes from OpenF1 that mean the flap is physically open */
export const DRS_OPEN_VALUES = new Set([10, 12, 14]);

/** DRS code that means the driver is in a DRS zone but hasn't opened yet */
export const DRS_ELIGIBLE_VALUES = new Set([8]);

// ---------------------------------------------------------------------------
// Live race tracking types
// ---------------------------------------------------------------------------

/** A driver's current state during a live race */
export interface LiveDriver {
  driver_number: number;
  abbreviation: string;
  full_name: string;
  team: string;
  team_color: string;
  position: number;
  current_compound: string;
  tyre_age: number;
  compounds_used: string[];
  stops_completed: number;
  last_lap_time: number | null;
  gap_to_leader: number;
  interval: number;
}

/** A race control message from OpenF1 */
export interface RaceControlMessage {
  lap: number | null;
  message: string;
  category: string | null;
  flag: string | null;
  date: string | null;
}

/** A pit stop event from OpenF1 */
export interface PitEvent {
  driver_number: number;
  lap: number;
  duration_s: number | null;
  date: string | null;
}

/** A single recommended strategy from mid-race recalculation */
export interface LiveStrategy {
  name: string;
  total_time_s: number;
  num_stops: number;
  pit_laps: number[];
  stints: Stint[];
  rank: number;
  gap_to_best_s: number;
}

/** Full race state — this is what the SSE stream sends */
export interface LiveRaceState {
  session_key: number | null;
  current_lap: number;
  total_laps: number;
  is_safety_car: boolean;
  last_race_control_message: string;
  drivers: Record<number, LiveDriver>;
  race_control_log: RaceControlMessage[];
  pit_log: PitEvent[];
  /** Per-driver strategy recommendations (keyed by driver_number) */
  strategies: Record<number, LiveStrategy[]>;
  last_updated: string | null;
  connected_to_openf1: boolean;
  polling_active: boolean;
  /** Per-driver telemetry (speed, rpm, gear, etc.) — only present with sponsor tier */
  car_data: Record<number, TelemetryData>;
  /** Whether the backend is polling telemetry endpoints (sponsor tier only) */
  telemetry_available: boolean;
  /** True when viewing a replay (vs live) */
  replay_mode: boolean;
  /** Playback speed multiplier (0=paused, 1/2/4/8) */
  replay_speed: number;
  /** 0-100 progress through the replay */
  replay_elapsed_pct: number;
  /** Circuit outline points for the track map — fetched once during replay setup */
  track_outline: TrackPoint[] | null;
  /** Chronological location records for frontend animation (one cycle's worth) */
  location_buffer: LocationBufferEntry[];
  /** Chronological car_data records for frontend animation (one cycle's worth) */
  car_data_buffer: CarDataBufferEntry[];
  /** Frontend-only fields added by the SSE hook */
  connected: boolean;
  lastUpdate: number;
}

/** Response from GET /api/live/status/{year}/{grand_prix} */
export interface LiveStatusResponse {
  session_key: number | null;
  total_laps: number | null;
  polling_active: boolean;
  current_session_key: number | null;
}

/** A driver entry in the teams list */
export interface LiveDriverInfo {
  number: string;
  abbreviation: string;
  full_name: string;
}

/** A team with its two drivers */
export interface LiveTeam {
  team: string;
  team_color: string | null;
  drivers: LiveDriverInfo[];
}

/** Response from GET /api/live/drivers/{year}/{grand_prix} */
export interface LiveDriversResponse {
  teams: LiveTeam[];
}
