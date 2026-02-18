# CLAUDE.md — F1 Race Strategy Tool

## What this project is

A web-based F1 race strategy tool that helps fans build and compare pit stop strategies using real practice session data. Users select a race weekend, explore tyre degradation / fuel loads / weather from practice and qualifying, then build and compare pit stop strategies.

## Tech stack

- **Backend:** Python 3.12 with FastAPI, serving REST API endpoints
- **Data sources:** FastF1 (historical session data, lap times, tyre info) and OpenF1 (live timing and telemetry)
- **Frontend:** React + Vite, managed by Deno 2.x (calls the backend REST API — no server-side rendering)
- **Deployment:** Single Docker container — nginx serves the React build and reverse-proxies `/api/*` to uvicorn

## Project layout

```
f1_strat/
├── Dockerfile              # Multi-stage: builds frontend, then runtime with Python + nginx
├── nginx.conf              # Reverse proxy: serves React, forwards /api/* to uvicorn
├── start.sh                # Entrypoint: runs uvicorn + nginx, traps signals
├── .dockerignore
├── backend/
│   ├── requirements.txt
│   ├── f1_strat/           # Python package — all backend source code
│   │   ├── api.py          # FastAPI REST endpoints
│   │   ├── cache.py        # FastF1 cache setup (must be called before loading data)
│   │   ├── degradation.py  # Tyre degradation analysis (fuel-corrected curves per compound)
│   │   ├── session_service.py  # Core data service wrapping FastF1 (incl. Q2 compound extraction)
│   │   ├── strategy.py     # Race strategy engine (lap simulation, pit stop optimization)
│   │   └── validation.py   # Backtesting engine vs actual race results (run with --years 2024)
│   └── tests/
│       ├── test_degradation.py
│       ├── test_session_service.py
│       ├── test_strategy.py
│       └── test_validation.py
└── frontend/               # React + Vite app, managed by Deno
    ├── deno.json           # Deno config: tasks (dev/build/lint) and nodeModulesDir
    ├── deno.lock           # Deno lock file (replaces package-lock.json)
    ├── package.json        # Dependency declarations (Deno reads this natively)
    ├── .env.development    # VITE_API_BASE for local dev without Docker
    └── src/
```

## Rules and conventions

### FastF1 caching is mandatory

Always call `setup_cache()` (from `f1_strat.cache`) before loading any FastF1 session data. FastF1 downloads from the F1 live timing API and caches locally to `backend/.fastf1_cache/`. Without caching, requests are slow and risk hitting rate limits.

In Docker, this path resolves to `/app/backend/.fastf1_cache` and is declared as a `VOLUME` so cached data survives container restarts. When running the container, mount a named volume there (e.g., `-v f1_cache:/app/backend/.fastf1_cache`).

When creating new modules or services that use FastF1, import and call `setup_cache()` early — the `SessionService.__init__` shows the pattern.

### Architecture: REST API backend, React frontend, nginx reverse proxy

The backend exposes REST API endpoints (FastAPI) that return JSON. The React frontend consumes these endpoints via relative URLs (no hardcoded host). In Docker, nginx serves the React static build on port 80 and reverse-proxies `/api/*` to uvicorn on port 8000. Keep this separation clean:

- Backend methods return plain Python dicts, not pandas DataFrames — this keeps them JSON-serializable
- Convert `pd.Timedelta` values to float seconds using `_td_to_seconds()` in session_service.py
- The frontend never calls FastF1 or OpenF1 directly
- The frontend uses `import.meta.env.VITE_API_BASE ?? ""` for the API base URL — empty string means relative URLs (correct for Docker). For local dev without Docker, `frontend/.env.development` sets it to `http://localhost:8000`

### Explain decisions in comments

The project owner is learning to code. When writing or modifying code:

- Add comments explaining *why* something is done, not just *what* it does
- Explain non-obvious library calls (e.g., why we pass `telemetry=False` to `session.load()`)
- Use docstrings on classes and public methods
- When there's a gotcha or pitfall (like FastF1's `pick_fastest()` returning `None`), add a comment noting it

### Run tests after changes

After modifying backend code, run the test suite:

```bash
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/ -v -s
```

The integration test hits the real F1 API on first run (~2-4 min) but uses the local cache after that (seconds). All tests should pass before considering a change complete.

### Building and running with Docker

Docker is the primary way to run the full app. After any code change, rebuild and run:

```bash
# Build the image (multi-stage: compiles React, then bundles with Python + nginx)
docker build -t f1-strat .

# Run the container — mount a volume so the FastF1 cache persists
docker run -d --name f1-strat -p 3000:80 -v f1_cache:/app/backend/.fastf1_cache f1-strat

# Verify
curl http://localhost:3000/api/schedule/2024     # JSON schedule
open http://localhost:3000                        # React frontend
open http://localhost:3000/docs                   # FastAPI interactive docs

# View logs (both uvicorn and nginx output)
docker logs f1-strat

# Stop and remove
docker stop f1-strat && docker rm f1-strat
```

If you only need the backend during development (no frontend), you can still run uvicorn directly:

```bash
PYTHONPATH=backend backend/venv/bin/uvicorn f1_strat.api:app --reload
```

### Running the frontend locally with Deno

The frontend uses Deno 2.x as its runtime. Deno reads `package.json` natively for dependencies and `deno.json` for task definitions. To run the frontend dev server:

```bash
cd frontend
deno install         # Install dependencies (generates deno.lock + node_modules/)
deno task dev        # Vite dev server at http://localhost:5173
deno task build      # Type-check with tsc, then build with Vite → dist/
deno task lint       # Run ESLint
```

`deno.json` has `"nodeModulesDir": "auto"` which tells Deno to create a `node_modules/` directory — this is required by Vite's plugin ecosystem.

Example endpoints:
- `GET /api/schedule/2024` — race calendar for the 2024 season
- `GET /api/degradation/2024/Spain` — tyre degradation curves for the 2024 Spanish GP
- `GET /api/strategy/2024/Spain?race_laps=66` — ranked pit stop strategies (66 laps)
- `GET /api/strategy/2024/Spain?race_laps=66&position_loss=3.0&max_stops=3` — with position loss penalty
- `GET /api/qualifying/2024/Spain` — Q2 tyre compounds for top-10 qualifiers
- `GET /api/weather/2024/Spain` — practice session weather summary

### Docker architecture notes

- `Dockerfile` is multi-stage: stage 1 (`denoland/deno:alpine`) builds the React app, stage 2 (`python:3.12-slim`) installs nginx + Python deps and copies everything in
- `nginx.conf` serves React from `/usr/share/nginx/html`, proxies `/api/`, `/docs`, and `/openapi.json` to uvicorn. Includes `proxy_read_timeout 300s` because FastF1 first-fetches can take 2-4 minutes
- `start.sh` runs uvicorn (2 workers) and nginx side-by-side, traps SIGTERM/SIGINT, and exits the container if either process crashes (so Kubernetes restarts it)
- Image size is ~800MB-1GB — normal for data-science Python (numpy, scipy, pandas)
- When adding new backend files, they're picked up automatically (the Dockerfile copies all of `backend/f1_strat/`)
- When adding new frontend dependencies, `deno install` in the Dockerfile reads `package.json` and `deno.lock` — make sure to commit the lock file

### FastF1 gotchas to know about

- `pick_fastest()` returns `None` (not an empty object) when no laps exist — always check the return value
- The cache directory must exist before `enable_cache()` is called — `setup_cache()` handles this via `mkdir(parents=True, exist_ok=True)`
- Load sessions with `telemetry=False` unless telemetry is specifically needed — it's much faster
- Sprint weekends don't have FP3 — the code handles this by catching exceptions in session loading
- Tyre compound values: SOFT, MEDIUM, HARD, INTERMEDIATE, WET, UNKNOWN
- Weather data is sampled ~once per minute, so there are only 1-2 weather points per lap
- **API rate limit: 500 calls/hour.** Running validation across many races can hit this. Use `--resume` to pick up where you left off after the limit resets

### Degradation analysis notes

- Practice data is inherently noisy — hard compound long runs may only have 2-3 stints per GP
- **Sprint race data is included** — `_load_practice_laps()` loads FP1, FP2, FP3, and Sprint ("S") sessions. Sprint laps are valuable because drivers push hard on race compounds for 25-30 laps. Missing sprints are expected on conventional weekends (logged at `debug` level, not `warning`)
- The first lap of each stint is always an out-lap (cold tyres) and must be skipped
- Hard tyres need 4-5 laps to warm up; softs warm up in ~1 lap. The algorithm finds "peak grip" automatically rather than using a fixed warm-up skip
- Fuel correction (default 0.07s/lap) is added to raw deltas because fuel burn-off masks degradation
- Within-stint outlier removal (median + 1.0s) is critical to filter traffic laps in practice
- Median averaging across stints (not mean) is more robust with small sample sizes
- When HARD data is missing from practice, it's estimated at 60% of the MEDIUM rate (with a 0.02 s/lap floor)
- When SOFT data is missing AND `starting_compound="SOFT"` is requested, estimated at 160% of MEDIUM rate (with a 0.05 floor). Only activated on-demand to avoid estimated SOFT rates polluting unconstrained predictions
- **Deg rate floor**: All compounds have a minimum 0.02 s/lap rate enforced in `_build_curves()`. Negative rates (from track evolution masking degradation in practice) are clamped — no tyre physically gets faster with age

### Strategy engine notes

- The strategy engine simulates every lap: `lap_time = base + (deg_rate × tyre_age) - (fuel_correction × laps_completed) - first_stint_bonus`
- Base lap time = fastest clean lap across FP1/FP2/FP3/Sprint (from `SessionService.get_base_lap_time()`)
- Pit stop loss (~22s) varies by circuit — Monaco ~25s, Monza ~20s
- Generates all permutations of compounds for 1-stop through 3-stop (default `max_stops=3`), filtered by FIA tyre regulations
- FIA rules require at least 2 different dry compounds per race — single-compound strategies (e.g., HARD→HARD) are excluded
- Event-specific rules: Monaco 2025 requires 2 mandatory pit stops; Qatar 2025 limits each tyre set to 25 laps
- Regulations are defined in `_TYRE_RULES` dict in strategy.py and looked up by `get_tyre_rules(year, event_name)`
- For each compound sequence, brute-force searches all valid pit lap combinations (minimum 4-lap stints, max stint length if regulated)
- Strategies are ranked by total predicted race time; gap_to_best_s shows the delta to the fastest option
- The response includes `regulations` dict showing which rules were applied

#### Pit stop cost model

Each pit stop incurs three costs beyond the pit lane time:

1. **Tyre warm-up penalty** (1.5s): New tyres need ~1 lap to reach operating temperature, making the out-lap slower
2. **Escalating position loss** (`position_loss_s`, default 3.0s): Each successive stop costs more because the driver encounters more traffic. Formula: `total = position_loss_s × N × (N+1) / 2` where N = number of stops. Stop 1 = 3s, stop 2 = 6s, stop 3 = 9s
3. **Tyre set allocation limits** (`_MAX_SETS_PER_COMPOUND`): HARD max 2, MEDIUM max 3, SOFT max 2 — prevents unrealistic multi-set strategies

The position loss penalty naturally discourages over-stopping without needing an artificial `max_stops` cap. The cap (default 3) is mainly a performance knob since 3-stop optimization is O(N³).

#### Practice-to-race deg scaling

Practice data systematically overestimates race degradation due to track evolution (less rubber), different track temperatures, and different car setups. The `deg_scaling` parameter (default 0.85) multiplies all practice-derived rates before simulation. Applied in both `_get_compound_config()` and the weather path. Exposed via API: `GET /api/strategy/2024/Spain?race_laps=66&deg_scaling=0.85`. Set to 1.0 to use raw practice rates.

#### Track position model (compound ordering)

With linear degradation, MEDIUM→HARD and HARD→MEDIUM have mathematically identical optimal race times (the optimizer finds mirrored pit laps). This is wrong — real teams overwhelmingly start softer and finish harder for track position.

Two mechanisms fix this:

1. **First-stint bonus**: A per-lap bonus applied only in the first stint, based on compound softness (`_COMPOUND_SOFTNESS`: SOFT=2, MEDIUM=1, HARD=0). At 0.05s/lap per tier (`_TRACK_POSITION_PACE_S`), a 25-lap MEDIUM first stint gets a 1.25s bonus.
2. **Last-stint penalty**: A flat 1.5s penalty per softness tier (`_LAST_STINT_SOFT_PENALTY_S`) when the final stint uses a softer compound than the penultimate. Fixes M→H→M vs M→H→H: real teams prefer harder final stints for lower degradation risk.

#### Q2 tyre rule (starting compound)

Top-10 qualifiers must start the race on the compound they used for their fastest Q2 lap. The `starting_compound` parameter filters strategies to only those beginning with that compound. `GET /api/qualifying/{year}/{gp}` returns each top-10 driver's Q2 compound.

### Validation

`validation.py` compares engine predictions against actual race winner strategies. Run with:

```bash
# Full season validation
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --years 2024

# Single race
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --race "Spanish Grand Prix" --years 2024

# Resume after rate limit
PYTHONPATH=backend backend/venv/bin/python -m f1_strat.validation --years 2025 --resume
```

Key metrics: stop count match rate, compound sequence match rate, compound set match rate, winner strategy rank, pit window accuracy. Results are saved to `backend/validation_results/`.

The validation also:
- Runs Q2-constrained predictions when the winner was a top-10 qualifier AND actually started on their Q2 compound
- Detects likely safety-car-influenced races (consecutive pit stops within 3 laps, or 4+ stops) and reports clean-race metrics separately
- Tracks compound balance (predicted vs actual SOFT/MEDIUM/HARD usage)

### Session types reference

| Code | Session          |
|------|------------------|
| FP1  | Practice 1       |
| FP2  | Practice 2       |
| FP3  | Practice 3       |
| Q    | Qualifying       |
| SQ   | Sprint Qualifying |
| S    | Sprint           |
| R    | Race             |
