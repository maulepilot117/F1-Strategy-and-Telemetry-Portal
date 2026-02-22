# F1 Race Strategy Tool

A web-based Formula 1 race strategy tool that predicts optimal pit stop strategies from real practice session data and tracks live races with real-time telemetry. Built with Python/FastAPI and React.

## Features

### Strategy Prediction Engine

Select any Grand Prix from 2023-2025, and the engine analyzes real practice and qualifying data to predict optimal pit stop strategies:

- **Tyre degradation analysis** — Fuel-corrected degradation curves per compound (SOFT, MEDIUM, HARD) built from FP1/FP2/FP3 and Sprint session lap times
- **Lap-by-lap simulation** — Models every lap accounting for tyre wear, fuel burn-off, cold tyre penalty, and track position effects
- **Multi-stop optimization** — Generates and ranks all valid 1/2/3-stop strategies across compound permutations, filtered by FIA tyre regulations
- **Mixed-weather strategies** — Define weather windows (dry/intermediate/wet) and the engine forces compound changes at transitions
- **Per-circuit pit loss** — Circuit-specific pit lane time loss (17s Austria to 27s Singapore) instead of a flat default
- **Historical stabilization** — Uses 3 years of race data at the same circuit to stabilize degradation curve shapes

### Live Race Tracking

Switch to Live mode during a race to follow a team's two drivers in real time:

- **Real-time telemetry** — Position, gap, interval, tyre compound, tyre age, lap times via Server-Sent Events
- **Team focus** — Select your team and see both drivers side-by-side
- **Mid-race strategy recalculation** — Engine recalculates optimal remaining strategies on every pit stop and safety car change, showing top 3 recommendations per driver
- **Safety car detection** — SC/VSC status tracked from race control messages
- **Race control log** — Live feed of flags, safety cars, and race director messages
- **Pit stop tracking** — Records every pit stop with lap number and duration

## How the Prediction Engine Works

The strategy engine simulates each lap of a race:

```
lap_time = base_lap_time
         + (deg_rate × tyre_age)
         - (fuel_correction × laps_completed)
         + warmup_penalty (first lap on new tyres)
         + position_loss (escalating per pit stop)
         - first_stint_bonus (softer compounds = better track position)
```

**Data pipeline:**
1. Load all practice session laps (FP1, FP2, FP3, Sprint) via FastF1
2. Filter to green-flag laps only (TrackStatus == "1"), skip out-laps, remove within-stint outliers
3. Weight laps by track temperature proximity to race conditions (Gaussian, sigma=10C)
4. Compute fuel-corrected degradation rates per compound using median averaging
5. Stabilize curvature with 3 years of historical race stint data at the same circuit
6. Apply practice-to-race scaling (0.85x) since practice systematically overestimates degradation
7. Brute-force search all valid pit lap combinations for each compound sequence
8. Rank strategies by total predicted race time

**Key engine parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `deg_scaling` | 0.85 | Practice-to-race degradation multiplier |
| `fuel_correction` | 0.055 s/lap | Fuel burn-off time gain per lap |
| `tyre_warmup_loss` | 1.0s | Cold tyre penalty per pit stop |
| `position_loss` | 3.0s | Escalating traffic penalty: stop 1 = 3s, stop 2 = 6s, stop 3 = 9s |
| `history_years` | 3 | Years of historical race data for curve stabilization |

**Validation results (2025 season, 21 races, 62 team comparisons across McLaren/Mercedes/Red Bull):**
- Stop count match: 53%
- Compound set match: 57%
- Compound balance: HARD +6, MEDIUM +1, SOFT -22 (slight over-prediction of harder compounds)
- Pit timing bias: +0.2 laps (nearly neutral)

## Tech Stack

- **Backend:** Python 3.12, FastAPI, FastF1 (historical F1 data), OpenF1 (live timing), httpx, sse-starlette
- **Frontend:** React 19, Vite, Recharts, Deno 2.x
- **Deployment:** Single Docker container — nginx serves the React build and reverse-proxies `/api/*` to uvicorn

## Quick Start with Docker

```bash
# Build the image
docker build -t f1-strat .

# Run — mount a volume so FastF1 cache persists across restarts
docker run -d --name f1-strat -p 3000:80 -v f1_cache:/app/backend/.fastf1_cache f1-strat

# Open in browser
open http://localhost:3000
```

## Local Development

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start the API server
PYTHONPATH=backend uvicorn f1_strat.api:app --reload
# API docs at http://localhost:8000/docs
```

### Frontend

Requires [Deno 2.x](https://deno.com).

```bash
cd frontend
deno install          # Install dependencies
deno task dev         # Vite dev server at http://localhost:5173
```

The frontend dev server proxies API calls to `http://localhost:8000` (configured in `.env.development`).

### Run Tests

```bash
# Backend tests (first run downloads F1 data, ~2-4 min; cached after that)
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/ -v -s

# Frontend type check + build
cd frontend && deno task build
```

## API Endpoints

### Strategy Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/schedule/{year}` | Race calendar for a season |
| GET | `/api/degradation/{year}/{grand_prix}` | Tyre degradation curves from practice data |
| GET | `/api/strategy/{year}/{grand_prix}?race_laps=N` | Ranked pit stop strategies (dry) |
| POST | `/api/strategy/{year}/{grand_prix}` | Strategies with weather windows (mixed conditions) |
| GET | `/api/qualifying/{year}/{grand_prix}` | Q2 tyre compounds for top-10 qualifiers |
| GET | `/api/weather/{year}/{grand_prix}` | Practice session weather summary |

### Live Race Tracking

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/live/status/{year}/{grand_prix}` | Check if a session is available for tracking |
| POST | `/api/live/start/{session_key}?total_laps=N&year=Y&grand_prix=GP` | Start polling OpenF1 with strategy recalculation |
| GET | `/api/live/drivers/{year}/{grand_prix}` | Teams and drivers for the team selector |
| GET | `/api/live/stream/{session_key}` | SSE stream of full race state snapshots |

## Project Structure

```
f1_strat/
├── Dockerfile                    # Multi-stage build (Deno → Python + nginx)
├── nginx.conf                    # Reverse proxy with SSE support
├── start.sh                      # Entrypoint: uvicorn + nginx
├── backend/
│   ├── requirements.txt
│   ├── f1_strat/
│   │   ├── api.py                # FastAPI REST + SSE endpoints
│   │   ├── cache.py              # FastF1 cache setup
│   │   ├── degradation.py        # Tyre degradation analysis
│   │   ├── session_service.py    # Core data service (FastF1 wrapper)
│   │   ├── strategy.py           # Race strategy engine
│   │   ├── live_race.py          # OpenF1 polling + SSE state management
│   │   └── validation.py         # Backtesting engine vs actual race results
│   └── tests/
│       ├── test_degradation.py
│       ├── test_strategy.py
│       ├── test_live_race.py     # 18 tests with mock OpenF1 fixtures
│       ├── test_validation.py
│       ├── test_session_service.py
│       └── fixtures/openf1/      # Recorded API responses for tests
└── frontend/
    ├── deno.json                 # Deno config (tasks, nodeModulesDir)
    ├── package.json              # Dependencies
    └── src/
        ├── App.tsx               # Main app with Analysis/Live mode toggle
        ├── api.ts                # Backend API client functions
        ├── types.ts              # TypeScript interfaces for all API shapes
        ├── hooks/
        │   └── useLiveRace.ts    # SSE hook (useSyncExternalStore)
        └── components/
            ├── RaceSelector.tsx          # Year + GP dropdown
            ├── DegradationChart.tsx      # Tyre degradation curves
            ├── StrategyControls.tsx      # Strategy parameter inputs
            ├── StrategyList.tsx          # Ranked strategy results
            ├── StrategyTimeline.tsx      # Visual stint timeline
            ├── WeatherScenarioBuilder.tsx # Weather window editor
            └── LiveDashboard.tsx         # Live race tracking view
```

## Live Tracking Architecture

The live tracking system polls the [OpenF1 API](https://openf1.org) and pushes state updates to the browser via Server-Sent Events:

```
OpenF1 API  →  Backend polling loop (8s intervals)  →  Module-level state dict
                                                            ↓
                                                    Strategy recalculation
                                                    (triggered by pit events
                                                     and SC/VSC changes)
                                                            ↓
Browser  ←  SSE (EventSourceResponse)  ←  Full state snapshot + strategy recommendations
```

- **Single worker** — `--workers 1` because race state lives in module-level memory
- **Automatic lifecycle** — Polling starts on first SSE client, stops after 5 minutes with no clients or when the race ends
- **Mid-race recalculation** — `calculate_remaining()` runs on pit events and safety car changes, using the driver's current compound, tyre age, and stops completed to find optimal strategies for the remaining laps
- **Coalesced triggers** — Rapid events (e.g., multiple cars pitting on the same lap) are coalesced into a single recalculation to avoid redundant work
- **Rate limit handling** — 429 responses trigger exponential backoff; 401/403 stops polling
- **Reconnection** — EventSource auto-reconnects; each SSE message is a full snapshot so no data is lost
- **Keepalive** — Server sends SSE comments every 15s during quiet periods to keep the connection alive through nginx

## Validation

Compare engine predictions against what leading teams actually did:

```bash
# Full season
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --years 2025

# Single race
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --race "Spanish Grand Prix" --years 2025

# Resume after API rate limit
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --years 2025 --resume
```

Compares against McLaren, Mercedes, and Red Bull strategies across all dry races. Key metrics: stop count match, compound sequence/set match, strategy rank, pit timing accuracy, compound balance.
