"""
REST API for the F1 strategy tool.

This is the layer between the React frontend and the Python data services.
The frontend makes HTTP requests to these endpoints and gets JSON back.
FastAPI was chosen because it automatically generates API docs, validates
inputs, and is one of the fastest Python web frameworks.

Run the server with:
    PYTHONPATH=backend uvicorn f1_strat.api:app --reload

Then visit http://localhost:8000/docs to see the interactive API docs.
"""

import asyncio
import json

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import fastf1

from f1_strat.cache import setup_cache
from f1_strat.degradation import DegradationService
from f1_strat import live_race
from f1_strat.session_service import SessionService
from f1_strat.strategy import StrategyEngine

# Create the FastAPI app — this is what uvicorn runs
app = FastAPI(
    title="F1 Race Strategy API",
    description="Tyre degradation analysis and race strategy data from real F1 sessions.",
    version="0.1.0",
)

# CORS middleware: allows the React frontend (running on a different port)
# to make requests to this API.  Without this, browsers block cross-origin
# requests for security reasons.  We allow all origins during development;
# in production you'd restrict this to your frontend's domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create services once — they set up the FastF1 cache on first use
_degradation_service = DegradationService()
_strategy_engine = StrategyEngine()
_session_service = SessionService()

# Ensure cache is ready for standalone FastF1 calls (like get_event_schedule)
setup_cache()


@app.get("/api/schedule/{year}")
def get_schedule(year: int) -> list[dict]:
    """Get the F1 race schedule for a given season.

    Returns a list of events with their names, locations, and dates.
    The frontend uses this to populate the Grand Prix dropdown.
    The `event_name` field is what gets passed to the degradation
    and strategy endpoints.

    **Example:** `/api/schedule/2024` returns all 2024 races.
    """
    # get_event_schedule returns a DataFrame of all events in the season
    schedule = fastf1.get_event_schedule(year)

    events = []
    for _, row in schedule.iterrows():
        # Skip testing events (round 0) — they aren't real race weekends
        if row["RoundNumber"] == 0:
            continue
        events.append({
            "round_number": int(row["RoundNumber"]),
            "event_name": row["EventName"],
            "country": row["Country"],
            "location": row["Location"],
            "date": str(row["EventDate"].date()) if hasattr(row["EventDate"], "date") else str(row["EventDate"]),
            "event_format": row["EventFormat"],
        })

    return events


@app.get("/api/degradation/{year}/{grand_prix}")
def get_degradation(
    year: int,
    grand_prix: str,
    fuel_correction: float = Query(
        default=0.07,
        ge=0.0,
        le=0.5,
        description=(
            "Seconds per lap to correct for fuel burn-off. "
            "The default 0.07 is a widely-used estimate. "
            "Set to 0 to see raw (uncorrected) degradation."
        ),
    ),
    history_years: int = Query(
        default=3,
        ge=0,
        le=5,
        description=(
            "Years of historical race data to use for stabilizing the "
            "quadratic degradation coefficient. Historical races provide "
            "abundant stints for reliable curvature detection. "
            "Set to 0 to use practice data only."
        ),
    ),
) -> dict:
    """Get tyre degradation curves for a Grand Prix weekend.

    Analyzes practice session laps (FP1, FP2, FP3) and returns a degradation
    curve for each tyre compound, showing how much slower each compound gets
    per lap.

    **Example:** `/api/degradation/2024/Spain` returns the degradation data
    for the 2024 Spanish Grand Prix.
    """
    return _degradation_service.analyze(
        year=year,
        grand_prix=grand_prix,
        fuel_correction_s=fuel_correction,
        history_years=history_years,
    )


@app.get("/api/weather/{year}/{grand_prix}")
def get_weather(year: int, grand_prix: str) -> dict:
    """Weather summary for all practice sessions.

    Returns temperature ranges, humidity, wind, and a 'had_rain' flag
    for each practice session (FP1, FP2, FP3) plus an overall summary.
    Useful for deciding whether to run the strategy engine in wet mode.

    **Example:** `/api/weather/2024/Spain`
    """
    return _session_service.get_weather_summary(year=year, grand_prix=grand_prix)


@app.get("/api/strategy/{year}/{grand_prix}")
def get_strategy(
    year: int,
    grand_prix: str,
    race_laps: int = Query(
        description="Total number of laps in the race (e.g., 66 for Spain).",
    ),
    pit_stop_loss: float | None = Query(
        default=None,
        ge=15.0,
        le=35.0,
        description=(
            "Seconds lost per pit stop (pit entry + stop + exit vs staying "
            "on track). When omitted, auto-selects a circuit-specific value "
            "(e.g., Austria 17s, Singapore 27s). Default ~22s."
        ),
    ),
    fuel_correction: float = Query(
        default=0.07,
        ge=0.0,
        le=0.5,
        description="Seconds per lap the car gets faster as fuel burns off.",
    ),
    conditions: str = Query(
        default="dry",
        description=(
            "Race conditions: 'dry' (default), 'intermediate', or 'wet'. "
            "Intermediate uses only the INTERMEDIATE compound; wet uses "
            "both WET and INTERMEDIATE."
        ),
    ),
    intermediate_deg_rate: float = Query(
        default=0.12,
        ge=0.0,
        le=1.0,
        description=(
            "Default degradation rate (s/lap) for INTERMEDIATE compound. "
            "Used when no real practice data is available."
        ),
    ),
    wet_deg_rate: float = Query(
        default=0.15,
        ge=0.0,
        le=1.0,
        description=(
            "Default degradation rate (s/lap) for WET compound. "
            "Used when no real practice data is available."
        ),
    ),
    max_stops: int = Query(
        default=3,
        ge=1,
        le=3,
        description=(
            "Maximum pit stops to consider (1-3). Default 3. "
            "The position_loss penalty naturally discourages over-stopping, "
            "so 3 is safe. Set to 2 for faster computation."
        ),
    ),
    position_loss: float = Query(
        default=3.0,
        ge=0.0,
        le=15.0,
        description=(
            "Escalating seconds lost per pit stop due to track position "
            "loss and traffic. Stop 1 costs 1×, stop 2 costs 2×, etc. "
            "Default 3.0s. Set to 0 to disable."
        ),
    ),
    starting_compound: str | None = Query(
        default=None,
        description=(
            "Force strategies to start on this compound (e.g., 'SOFT'). "
            "Used for the Q2 tyre rule: top-10 qualifiers must start on "
            "their Q2 fastest-lap compound."
        ),
    ),
    deg_scaling: float = Query(
        default=0.85,
        ge=0.5,
        le=1.0,
        description=(
            "Multiplier applied to practice-derived deg rates to account "
            "for practice-vs-race differences (track evolution, rubber "
            "build-up). Default 0.85 (15%% reduction). Set to 1.0 to use "
            "raw practice rates."
        ),
    ),
) -> dict:
    """Get ranked pit stop strategies for a race.

    Generates strategies using available tyre compounds, optimizes the
    pit lap for each, and ranks them by predicted total race time.

    In **dry** mode: uses SOFT/MEDIUM/HARD with standard FIA rules.
    In **intermediate** mode: uses INTERMEDIATE only, with relaxed rules.
    In **wet** mode: uses both WET and INTERMEDIATE compounds.

    **Examples:**
    - `/api/strategy/2024/Spain?race_laps=66` — dry (default)
    - `/api/strategy/2024/Spain?race_laps=66&conditions=intermediate`
    - `/api/strategy/2024/Spain?race_laps=66&conditions=wet&wet_deg_rate=0.15`
    """
    # Validate the conditions parameter
    if conditions not in ("dry", "intermediate", "wet"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid conditions '{conditions}'. Must be 'dry', 'intermediate', or 'wet'.",
        )

    return _strategy_engine.calculate(
        year=year,
        grand_prix=grand_prix,
        race_laps=race_laps,
        pit_stop_loss_s=pit_stop_loss,
        fuel_correction_s=fuel_correction,
        conditions=conditions,
        intermediate_deg_rate=intermediate_deg_rate,
        wet_deg_rate=wet_deg_rate,
        max_stops=max_stops,
        position_loss_s=position_loss,
        starting_compound=starting_compound,
        deg_scaling=deg_scaling,
    )


@app.get("/api/qualifying/{year}/{grand_prix}")
def get_qualifying(year: int, grand_prix: str) -> dict:
    """Get Q2 tyre compound data for top-10 qualifiers.

    Returns each top-10 qualifier's fastest Q2 lap compound and the most
    common compound.  This is used for the Q2 tyre rule: top-10 qualifiers
    must start the race on the compound they used for their fastest Q2 lap.

    **Example:** `/api/qualifying/2024/Spain`
    """
    return _session_service.get_q2_compounds(year=year, grand_prix=grand_prix)


# ---------------------------------------------------------------------------
# POST endpoint for mixed-weather strategy calculation
# ---------------------------------------------------------------------------

class WeatherWindowInput(BaseModel):
    """A weather window: a range of laps under one weather condition.

    Windows must tile the full race distance (1 through race_laps)
    with no gaps or overlaps.
    """
    start_lap: int = Field(ge=1, description="First lap of this weather window (1-indexed).")
    end_lap: int = Field(ge=1, description="Last lap of this weather window (inclusive).")
    condition: str = Field(
        description="Weather condition: 'dry', 'intermediate', or 'wet'."
    )


class StrategyRequest(BaseModel):
    """Request body for the mixed-weather strategy endpoint.

    All fields have sensible defaults except race_laps, which varies by
    circuit (e.g., 66 for Spain, 78 for Monaco).
    """
    race_laps: int = Field(ge=10, le=100, description="Total laps in the race.")
    pit_stop_loss: float | None = Field(
        default=None, ge=15.0, le=35.0,
        description=(
            "Seconds lost per pit stop. When null/omitted, auto-selects "
            "a circuit-specific value (e.g., Austria 17s, Singapore 27s)."
        ),
    )
    fuel_correction: float = Field(
        default=0.07, ge=0.0, le=0.5,
        description="Seconds per lap the car gets faster as fuel burns off.",
    )
    intermediate_deg_rate: float = Field(
        default=0.12, ge=0.0, le=1.0,
        description="Default INTERMEDIATE degradation rate (s/lap).",
    )
    wet_deg_rate: float = Field(
        default=0.15, ge=0.0, le=1.0,
        description="Default WET degradation rate (s/lap).",
    )
    weather_windows: list[WeatherWindowInput] | None = Field(
        default=None,
        description=(
            "Weather windows defining changing conditions during the race. "
            "When provided, the engine runs a mixed-condition simulation "
            "with mandatory pit stops at weather transitions. "
            "When null/omitted, runs a standard dry simulation."
        ),
    )
    max_stops: int = Field(
        default=3, ge=1, le=3,
        description=(
            "Maximum pit stops to consider (1-3). Default 3. "
            "Position loss penalty naturally discourages over-stopping."
        ),
    )
    position_loss: float = Field(
        default=3.0, ge=0.0, le=15.0,
        description=(
            "Escalating seconds lost per pit stop due to track position "
            "loss. Stop 1 costs 1×, stop 2 costs 2×, etc. Default 3.0s."
        ),
    )
    starting_compound: str | None = Field(
        default=None,
        description=(
            "Force strategies to start on this compound (e.g., 'SOFT'). "
            "Used for the Q2 tyre rule."
        ),
    )
    deg_scaling: float = Field(
        default=0.85, ge=0.5, le=1.0,
        description=(
            "Multiplier applied to practice-derived deg rates to account "
            "for practice-vs-race differences. Default 0.85 (15% reduction)."
        ),
    )


@app.post("/api/strategy/{year}/{grand_prix}")
def post_strategy(year: int, grand_prix: str, body: StrategyRequest) -> dict:
    """Calculate strategies with optional mixed-weather conditions.

    This POST endpoint accepts a JSON body with weather windows, allowing
    users to simulate races where weather changes mid-race.  When
    weather_windows is provided, the engine forces pit stops at weather
    transitions and picks the best compounds for each condition.

    When weather_windows is null or omitted, behaves identically to the
    GET endpoint in dry mode.

    **Example POST body for a dry→rain→dry race:**
    ```json
    {
      "race_laps": 66,
      "weather_windows": [
        {"start_lap": 1, "end_lap": 19, "condition": "dry"},
        {"start_lap": 20, "end_lap": 40, "condition": "intermediate"},
        {"start_lap": 41, "end_lap": 66, "condition": "dry"}
      ]
    }
    ```
    """
    # Convert weather windows from Pydantic models to plain dicts
    # (the strategy engine works with dicts, not Pydantic models)
    windows = None
    if body.weather_windows is not None:
        windows = [w.model_dump() for w in body.weather_windows]

    try:
        return _strategy_engine.calculate(
            year=year,
            grand_prix=grand_prix,
            race_laps=body.race_laps,
            pit_stop_loss_s=body.pit_stop_loss,
            fuel_correction_s=body.fuel_correction,
            intermediate_deg_rate=body.intermediate_deg_rate,
            wet_deg_rate=body.wet_deg_rate,
            weather_windows=windows,
            max_stops=body.max_stops,
            position_loss_s=body.position_loss,
            starting_compound=body.starting_compound,
            deg_scaling=body.deg_scaling,
        )
    except ValueError as e:
        # Weather window validation errors → 400 Bad Request
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Shutdown handler — clean up the httpx connection pool
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
async def shutdown() -> None:
    """Clean up on server shutdown: stop polling and close the HTTP client."""
    await live_race.stop_polling()
    await live_race.close_client()


# ---------------------------------------------------------------------------
# Live race tracking endpoints
# ---------------------------------------------------------------------------

@app.get("/api/live/status/{year}/{grand_prix}")
async def get_live_status(
    year: int,
    grand_prix: str,
    session_type: str = Query(default="Race", description="Session type (Race or Sprint)"),
) -> dict:
    """Check if a race session is available for live tracking.

    Resolves the year + grand_prix to an OpenF1 session_key by querying
    the OpenF1 /sessions endpoint.  Also returns total_laps from FastF1
    and whether polling is already active.

    **Example:** `/api/live/status/2024/Spain`
    """
    # Resolve session_key from OpenF1
    # OpenF1 uses country_name, but FastF1 event names are like "Spanish Grand Prix".
    # Try the grand_prix value directly as country_name first.
    session_key = await live_race.resolve_session_key(year, grand_prix, session_type)

    # Get total laps from FastF1's race data (if available)
    total_laps = None
    try:
        race_info = _session_service.get_race_info(year, grand_prix)
        if race_info:
            total_laps = race_info.get("total_laps")
    except Exception:
        pass

    return {
        "session_key": session_key,
        "total_laps": total_laps,
        "polling_active": live_race._race_state.get("polling_active", False),
        "current_session_key": live_race._race_state.get("session_key"),
    }


@app.post("/api/live/start/{session_key}")
async def start_live_tracking(
    session_key: int,
    total_laps: int = Query(description="Total laps in the race"),
    year: int | None = Query(default=None, description="Season year for strategy recalculation"),
    grand_prix: str | None = Query(default=None, description="Grand Prix name for strategy recalculation"),
) -> dict:
    """Start polling OpenF1 for live race data.

    Idempotent — if already polling the same session, returns current status
    without restarting.  If polling a different session, stops the old one
    and starts the new one.

    The year and grand_prix params are used for mid-race strategy recalculation.
    When provided, the engine loads practice degradation data for the circuit
    and recalculates optimal strategies as the race unfolds.

    **Example:** `POST /api/live/start/9539?total_laps=66&year=2024&grand_prix=Spain`
    """
    await live_race.start_polling(session_key, total_laps, year, grand_prix)

    return {
        "status": "polling",
        "session_key": session_key,
        "total_laps": total_laps,
        "drivers": len(live_race._race_state.get("drivers", {})),
    }


@app.get("/api/live/drivers/{year}/{grand_prix}")
def get_live_drivers(year: int, grand_prix: str) -> dict:
    """Get teams and drivers for the team selector dropdown.

    Returns teams grouped with their two drivers, using data from FastF1's
    session results (same source as the existing qualifying endpoint).

    **Example:** `/api/live/drivers/2024/Spain`
    """
    try:
        # Load any session to get driver/team info (qualifying has the best data)
        session = fastf1.get_session(year, grand_prix, "Q")
        session.load(laps=False, telemetry=False, weather=False, messages=False)
        drivers = _session_service._extract_drivers(session)
    except Exception:
        # Fall back to race session if qualifying isn't available
        try:
            session = fastf1.get_session(year, grand_prix, "R")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
            drivers = _session_service._extract_drivers(session)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Could not load driver data: {e}")

    # Group drivers by team
    teams: dict[str, dict] = {}
    for d in drivers:
        team_name = d.get("team", "Unknown")
        if team_name not in teams:
            teams[team_name] = {
                "team": team_name,
                "team_color": d.get("team_color"),
                "drivers": [],
            }
        teams[team_name]["drivers"].append({
            "number": d.get("number"),
            "abbreviation": d.get("abbreviation"),
            "full_name": d.get("full_name"),
        })

    return {"teams": list(teams.values())}


@app.get("/api/live/stream/{session_key}")
async def live_stream(request: Request, session_key: int) -> EventSourceResponse:
    """Server-Sent Events stream of full race state snapshots.

    Sends the complete race state as JSON on every update (~every 8 seconds).
    On initial connect or reconnect, the first event is the current state.
    Sends a keepalive comment every 15 seconds during quiet periods.

    The frontend uses EventSource to connect — it auto-reconnects on
    disconnection and every message is a full snapshot, so no state is lost.

    **Example:** `GET /api/live/stream/9539` (use with EventSource, not fetch)
    """
    # Verify we're tracking this session
    if live_race._race_state.get("session_key") != session_key:
        raise HTTPException(
            status_code=404,
            detail=f"Not tracking session {session_key}. Start polling first.",
        )

    async def event_generator():
        """Yield SSE events — full state snapshots + keepalive comments."""
        live_race._sse_client_count += 1
        last_sent = None
        keepalive_counter = 0

        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                current = live_race._race_state.get("last_updated")

                if current != last_sent:
                    # State changed — send full snapshot
                    last_sent = current
                    keepalive_counter = 0
                    yield {
                        "event": "message",
                        "data": json.dumps(live_race._race_state, default=str),
                    }
                else:
                    keepalive_counter += 1
                    # Send keepalive every 15 seconds (15 × 1s sleep)
                    if keepalive_counter >= 15:
                        keepalive_counter = 0
                        yield {"comment": "keepalive"}

                await asyncio.sleep(1)
        finally:
            live_race._sse_client_count -= 1

    return EventSourceResponse(event_generator())
