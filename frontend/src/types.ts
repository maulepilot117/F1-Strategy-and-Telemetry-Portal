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
  last_updated: string | null;
  connected_to_openf1: boolean;
  polling_active: boolean;
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
