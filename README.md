# F1 Race Strategy Tool

A web-based Formula 1 race strategy tool. Select a race weekend, explore practice and qualifying session data, and build pit stop strategies based on tyre degradation, fuel loads, and weather conditions.

**Status:** Early development — the data layer is working, UI is coming next.

## What it does today

The Python backend uses [FastF1](https://github.com/theOehrly/Fast-F1) to pull real session data from the F1 live timing API. For any Grand Prix, you can load:

- **Lap data** — times, sectors, tyre compound, tyre age, stint number, and position for every lap by every driver
- **Weather** — air/track temperature, humidity, wind, and rainfall sampled every ~60 seconds
- **Driver info** — names, numbers, teams, and team colours
- **Results** — finishing positions and qualifying times (Q1/Q2/Q3)

Data is cached locally after the first download, so subsequent loads are instant.

## Prerequisites

- **Python 3.10 or newer** (check with `python3 --version`)
- **pip** (comes with Python)

## Setup

1. **Open a terminal** and navigate to this project folder.

2. **Create a virtual environment** (keeps dependencies isolated from your system Python):

   ```bash
   cd backend
   python3 -m venv venv
   ```

3. **Activate the virtual environment:**

   ```bash
   # macOS / Linux
   source venv/bin/activate

   # Windows
   venv\Scripts\activate
   ```

   You'll see `(venv)` appear at the start of your terminal prompt — that means it's active.

4. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

## Run the tests

```bash
cd backend
pytest tests/ -v -s
```

**First run** takes 2–4 minutes (downloading data from the F1 API).
**Subsequent runs** use the local cache and finish in seconds.

The tests pull real data from two Grand Prix weekends and print summaries showing lap counts, tyre compounds, temperatures, fastest laps, and degradation curves.

## Start the API server

```bash
cd backend
uvicorn f1_strat.api:app --reload
```

Then visit http://localhost:8000/docs to see the interactive API documentation.

**Try it:** open http://localhost:8000/api/degradation/2024/Spain in your browser to see tyre degradation data for the 2024 Spanish Grand Prix.

## Project structure

```
f1_strat/
├── README.md               ← you are here
├── backend/
│   ├── requirements.txt    ← Python dependencies
│   ├── f1_strat/
│   │   ├── api.py          ← REST API endpoints (FastAPI)
│   │   ├── cache.py        ← FastF1 cache configuration
│   │   ├── degradation.py  ← tyre degradation analysis
│   │   └── session_service.py  ← core data service
│   └── tests/
│       ├── test_degradation.py      ← degradation analysis test
│       └── test_session_service.py  ← session data test
└── frontend/               ← React app (coming soon)
```

## What's next

- React UI for browsing race weekends and sessions
- Pit stop strategy builder using degradation data
- Weather impact visualisation
- Live race telemetry support
# f1_strat
