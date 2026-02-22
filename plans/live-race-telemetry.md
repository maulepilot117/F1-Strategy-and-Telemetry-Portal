# feat: Live Race Tracking with Real-Time Strategy Recalculation

## Overview

Add a live race mode to the F1 strategy tool. Users select a team, see both drivers' race data side by side (position, gap, lap times, tyre compound/age), and get up to 3 real-time strategy recommendations with pit windows. When safety cars or pit stops occur, the engine recalculates for the remaining race distance.

## Problem Statement / Motivation

The current tool analyzes historical practice data and produces pre-race strategy predictions. Once the race starts, users have no way to see how the strategy is unfolding or get updated recommendations as conditions change. Real F1 strategists continuously adapt their plans during the race — this feature brings that experience to fans.

## Technical Approach

### Architecture

```
OpenF1 REST API (api.openf1.org/v1)
    ^
    | httpx async polling (~8s interval, ~37 req/min on sponsor tier)
    |
Backend: FastAPI + polling background task + module-level state dict
    |
    | SSE (Server-Sent Events) — full state snapshots + keepalive
    |
nginx (proxy_buffering off for /api/live/)
    |
    v
React Frontend: EventSource → useSyncExternalStore
```

**Why SSE over WebSocket:**
- Data flow is unidirectional (server → client)
- `EventSource` API handles reconnection automatically
- Simpler nginx config (standard HTTP, just disable buffering)
- Easier to test (`curl -N` shows events in plaintext)

**Why not poll from the frontend directly:**
- OpenF1 sponsor tier: 6 req/s, 60 req/min — a single backend poller fans out to all connected browser tabs
- The backend can correlate data across endpoints (e.g., pit event + next stint = new compound)
- Strategy recalculation happens server-side anyway

**Key constraints:**
- **One race at a time.** The backend tracks a single live session in a module-level dict. Appropriate for a fan tool on a home Kubernetes lab.
- **Single uvicorn worker required.** Module-level `_race_state` is in-process memory. With 2+ workers, the polling task runs in one process but SSE endpoints may be served by another (with empty state). `start.sh` must use `--workers 1` when live mode is active.

### OpenF1 Rate Limit Strategy

Sponsor tier (€9.90/month) allows 60 req/min. Poll all 5 endpoints at ~8 second intervals via round-robin — that gives ~37 req/min, well under the limit. Tune intervals based on real-world testing if needed.

Endpoints: `/v1/race_control`, `/v1/pit`, `/v1/stints`, `/v1/position`, `/v1/intervals`.

**Error handling:**
- HTTP 429: respect `Retry-After`, back off to 30s → 60s → stop polling, show "Data source temporarily unavailable"
- HTTP 401/403: stop polling immediately (auth/subscription issue), show "OpenF1 API key invalid"
- HTTP 500/timeout: log warning, keep last known state, show "Data may be delayed"

### Session Key Resolution

The frontend sends `year` + `grand_prix` (e.g., `2025/Spain`). The backend resolves this to an OpenF1 `session_key` by querying:

```
GET https://api.openf1.org/v1/sessions?year=2025&country_name=Spain&session_name=Race
```

This returns a list with one entry containing the integer `session_key`. Cache the result for the session lifetime. If no session is found or the session hasn't started, return an appropriate status to the frontend.

### SSE Design

**One event type: full state snapshot** on every change. This eliminates partial update merging bugs, missing event reconciliation, and special reconnection logic.

**Keepalive:** Send a `: keepalive\n\n` SSE comment every 15 seconds during quiet periods (formation laps, long SC/red flag periods) to prevent proxy/browser connection drops.

**Reconnection:** On reconnect, `EventSource` sends `Last-Event-ID`. The server ignores it — every message is a full snapshot, so reconnection is free. No event ID tracking needed.

### Frontend State

Use `useSyncExternalStore` (React 19 built-in) for the SSE data store:

```
EventSource.onmessage → setState(JSON.parse(e.data)) → notify listeners
```

No Zustand, no Redux, no external dependencies. The live dashboard is a separate "mode" toggled via a tab at the top of the app (no React Router needed).

### Display: What "Live Race Tracking" Shows

Given rate limit constraints (no car_data at 3.7 Hz), the display focuses on **strategy-relevant race data**, not raw speed/throttle traces:

For each driver on the selected team:
- Current position and gap to leader / car ahead
- Current compound and tyre age (laps on current set)
- Last lap time
- Pit stops completed (lap number and duration)

Shared panel:
- Race control feed (SC/VSC/flag messages displayed verbatim from OpenF1)
- Strategy recommendations (up to 3, with pit windows) — added in Phase 2
- Connection status indicator (connected / reconnecting)

### Polling Lifecycle

1. **Start:** User clicks "Go!" on the live dashboard → frontend calls `POST /api/live/start/{session_key}` → backend starts an `asyncio.Task` that polls OpenF1 in a loop
2. **Run:** Polling task runs continuously, updating `_race_state` each cycle. SSE endpoint reads the state dict on each heartbeat/change.
3. **Stop:** Polling stops when: (a) `current_lap >= total_laps` (race finished), or (b) no SSE clients connected for 5 minutes (tracked via a simple connection counter), or (c) the server shuts down
4. **Idempotent start:** If polling is already running for the same `session_key`, `POST /api/live/start` is a no-op and returns the current status

### Development Without a Live Race

OpenF1 keeps historical session data accessible via the same endpoints — pass a `session_key` from a past race and the backend polls, gets all data immediately, updates state, and streams SSE. Tests use `httpx.MockTransport` with recorded JSON fixtures for deterministic offline testing.

Full replay mode (with speed controls, pause/resume, time-delayed playback) is deferred to v2.

---

## Implementation Phases

### Phase 1: Backend Polling + SSE + Frontend Display

**Goal:** Poll OpenF1, maintain race state, expose SSE stream, configure nginx, and build the frontend dashboard showing live race data. No strategy recalculation — pure display.

**New files:**

| File | Purpose |
|------|---------|
| `backend/f1_strat/live_race.py` | OpenF1 polling functions, race state dict, SSE generator |
| `backend/tests/test_live_race.py` | Tests with recorded API response fixtures |
| `backend/tests/fixtures/openf1/` | Recorded OpenF1 API responses for a historical race |
| `frontend/src/hooks/useLiveRace.ts` | SSE connection + `useSyncExternalStore` |
| `frontend/src/components/LiveDashboard.tsx` | Entire live race view (team selector, driver cards, race control) |

**Modified files:**

| File | Change |
|------|--------|
| `backend/requirements.txt` | Add `httpx>=0.27`, `sse-starlette>=2.0` |
| `backend/f1_strat/api.py` | Add SSE endpoint, live status/start/drivers endpoints, shutdown handler |
| `nginx.conf` | Add `/api/live/` location block with SSE directives |
| `start.sh` | Use `--workers 1` (required for module-level state) |
| `frontend/src/App.tsx` | Add mode toggle (Analysis / Live) |
| `frontend/src/api.ts` | Add `fetchLiveDrivers()`, `fetchLiveStatus()`, `startLiveTracking()` |
| `frontend/src/types.ts` | Add live race interfaces (no separate `liveTypes.ts`) |

#### `live_race.py` — OpenF1 Polling + Race State

Module-level functions and a shared state dict. The exact dict shape will be determined by what OpenF1 returns, but tracks: per-driver data (position, compound, tyre age, lap time, gap) and session-level data (safety car status, race control messages, connection status).

Key functions:

```python
# --- HTTP client ---
_http_client: httpx.AsyncClient | None = None

async def _get_client() -> httpx.AsyncClient:
    """Lazy-init a shared async HTTP client with 30s timeout."""

async def close_client() -> None:
    """Shutdown hook — close the httpx connection pool."""

# --- OpenF1 fetching ---
async def fetch_openf1(endpoint: str, params: dict | None = None) -> list[dict]:
    """Fetch from OpenF1 API. Handles 429 with backoff, 401/403 stops polling."""

# --- Polling ---
async def start_polling(session_key: int, total_laps: int) -> None:
    """Poll OpenF1 endpoints in round-robin at ~8s intervals, updating _race_state."""

async def stop_polling() -> None:
    """Stop the polling task."""

# --- State updates ---
def _update_safety_car_status(messages: list[dict]) -> None:
    """Check race control messages for SC/VSC keywords, update is_safety_car flag."""

# --- SSE ---
async def race_state_generator(request: Request):
    """Yield full _race_state snapshots. Sends keepalive comments every 15s during quiet periods."""
```

Note: `fetch_openf1` takes an explicit `params: dict` (not `**kwargs`) so OpenF1's special query operators like `date>` are passed naturally as dict keys.

#### API Endpoints

```python
@app.get("/api/live/status/{year}/{grand_prix}")
def get_live_status(year: int, grand_prix: str, session_type: str = "Race") -> dict:
    """Resolve session_key via OpenF1 /v1/sessions endpoint.
    Returns session_key, total_laps, and whether polling is active."""

@app.post("/api/live/start/{session_key}")
async def start_live_tracking(session_key: int, total_laps: int) -> dict:
    """Start polling. Idempotent — no-op if already polling this session."""

@app.get("/api/live/drivers/{year}/{grand_prix}")
def get_live_drivers(year: int, grand_prix: str) -> dict:
    """Teams with their two drivers (name, number, team color)."""

@app.get("/api/live/stream/{session_key}")
async def live_stream(request: Request, session_key: int) -> EventSourceResponse:
    """SSE stream of full state snapshots. Sends keepalive every 15s."""
```

Shutdown handler in `api.py`:
```python
@app.on_event("shutdown")
async def shutdown():
    await live_race.close_client()
    await live_race.stop_polling()
```

#### nginx.conf Addition

```nginx
# SSE proxy for live race data.
# nginx uses longest prefix match, so /api/live/ naturally takes
# priority over /api/ without needing specific ordering.
location /api/live/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
    add_header X-Accel-Buffering no;

    # Let EventSource auto-reconnect on timeout — every message
    # is a full snapshot, so reconnection is free.
    proxy_read_timeout 300s;
}
```

#### Frontend — `useLiveRace.ts`

The `useSyncExternalStore` pattern (kept because it's non-obvious for a learning coder):

```typescript
let state: LiveRaceState = { /* initial empty state */ };
const listeners = new Set<() => void>();

function connectToRace(sessionKey: number): () => void {
    const apiBase = import.meta.env.VITE_API_BASE ?? "";
    const es = new EventSource(`${apiBase}/api/live/stream/${sessionKey}`);

    es.onmessage = (e) => {
        state = { ...JSON.parse(e.data), connected: true, lastUpdate: Date.now() };
        listeners.forEach((l) => l());
    };
    es.onerror = () => {
        state = { ...state, connected: false };
        listeners.forEach((l) => l());
    };

    return () => es.close();
}

export function useLiveRace(sessionKey: number | null): LiveRaceState {
    useEffect(() => {
        if (!sessionKey) return;
        return connectToRace(sessionKey);
    }, [sessionKey]);

    return useSyncExternalStore(
        (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
        () => state,
    );
}
```

#### Frontend — `LiveDashboard.tsx`

Single component for the entire live view. Layout:

```
+------------------------------------------------------------------+
| [Analysis] [Live]              F1 Race Strategy Tool              |
+------------------------------------------------------------------+
| Year: [2025 v]  Race: [Spanish GP v]  Team: [McLaren v]  [Go!]  |
+------------------------------------------------------------------+
| SC: GREEN FLAG              |  Lap 34/66  |  Connected ●         |
+------------------------------------------------------------------+
|  NOR (McLaren)              |  PIA (McLaren)                     |
|  P3  Gap: +3.456s           |  P7  Gap: +12.890s                 |
|  MEDIUM  Age: 12 laps       |  HARD  Age: 6 laps                |
|  Last: 1:18.456             |  Last: 1:19.012                    |
|  Pits: 1 (lap 22)           |  Pits: 1 (lap 28)                  |
+------------------------------------------------------------------+
| Race Control                                                      |
| Lap 30: GREEN FLAG                                                |
| Lap 28: PIT NOR                                                   |
| Lap 15: VSC DEPLOYED                                              |
+------------------------------------------------------------------+
```

Strategy panel is added in Phase 2.

#### Testing

- Test individual poll/update functions with `httpx.MockTransport` and recorded JSON fixtures
- Do NOT test the async polling loop itself — test the functions it calls in isolation
- Record fixtures once from a completed historical race (e.g., 2024 Spanish GP) and commit them
- SSE endpoint tested with `httpx.AsyncClient(app=app)` (ASGI transport) iterating the async stream
- Use `logging.getLogger(__name__)` for all logging (polling status, rate limit events, connection counts)

**Deliverables:**
- [ ] `live_race.py` with polling functions, state dict, SC detection, SSE generator with keepalive
- [ ] `POST /api/live/start`, `GET /api/live/status`, `GET /api/live/drivers`, `GET /api/live/stream` endpoints
- [ ] FastAPI shutdown handler for `httpx.AsyncClient` cleanup
- [ ] nginx `/api/live/` location block (300s timeout)
- [ ] `start.sh` updated to `--workers 1`
- [ ] Tests with `httpx.MockTransport` and recorded fixtures
- [ ] `httpx` and `sse-starlette` added to requirements.txt
- [ ] `useLiveRace.ts` hook with `useSyncExternalStore`
- [ ] `LiveDashboard.tsx` with team selector, driver cards, race control feed
- [ ] Live race interfaces added to existing `types.ts`
- [ ] Mode toggle in `App.tsx`
- [ ] `fetchLiveDrivers()`, `fetchLiveStatus()`, `startLiveTracking()` in `api.ts`

---

### Phase 2: Strategy Engine + Live Strategy Panel

**Goal:** Enable mid-race strategy recalculation, wire it into the polling loop, and add the strategy panel to the frontend.

**Modified files:**

| File | Change |
|------|--------|
| `backend/f1_strat/strategy.py` | Add `calculate_remaining()` method, add params to `_simulate_race()` |
| `backend/f1_strat/live_race.py` | Add recalculation triggers (on pit events and SC changes) |
| `backend/tests/test_strategy.py` | Tests for mid-race recalculation |
| `frontend/src/components/LiveDashboard.tsx` | Add strategy recommendations panel |

#### Approach: Add Parameters, Don't Refactor

Add two parameters to `_simulate_race()` (the validated 160-line simulation loop stays intact):

- `start_lap: int = 0` — lap offset for fuel correction. When `start_lap > 0`, first-stint bonus is automatically skipped (no separate boolean needed)
- `initial_tyre_age: int = 0` — starting tyre age for the first stint

**Fuel correction for mid-race:** Keep `race_lap` as an absolute lap number (initialize to `start_lap + 1` instead of `1`). The existing formula `fuel_correction_s * (race_lap - 1)` already works correctly for both cases, since `race_lap` starts at the right offset.

**Position loss:** Use `N = remaining_stops` (not total race stops). One-line change in the position loss formula.

#### `calculate_remaining()` Method

```python
def calculate_remaining(
    self,
    year: int,
    grand_prix: str,
    current_lap: int,
    race_laps: int,
    current_compound: str,
    tyre_age: int,
    stops_completed: int,
    compounds_used: list[str],
    max_stops: int = 2,
    # ... other params same as calculate()
) -> dict:
    """Calculate optimal strategy from current race position.

    Key differences from calculate():
    - Simulates remaining_laps = race_laps - current_lap
    - First stint starts with current_compound at tyre_age
    - Fuel correction accounts for laps already completed (start_lap=current_lap)
    - FIA compound diversity checked against compounds_used
    - First-stint bonus skipped (derived from start_lap > 0)
    - Position loss counted from remaining stops only
    - Default max_stops=2 (keeps recalculation fast — O(N^2) not O(N^3))
    """
```

#### Recalculation Triggers in `live_race.py`

Recalculate for ALL drivers on pit events or SC status changes. Cache results in `_race_state["strategies"]` keyed by driver number. Frontend filters to the selected team.

```python
async def _maybe_recalculate() -> None:
    """Recalculate strategies for all drivers. Uses try/finally to ensure
    is_calculating is always cleared — if calculate_remaining() raises,
    a stuck flag would freeze all future recalculations."""
```

The `is_calculating` / `needs_recalc` coalescing pattern must use `try/finally` and must not have an `await` between `_needs_recalc = False` and the recursive call.

Profile `calculate_remaining()` with ~30 remaining laps and `max_stops=2` before adding `asyncio.run_in_executor()`. The search space is likely small enough to run synchronously.

**Deliverables:**
- [ ] `start_lap`, `initial_tyre_age` params on `_simulate_race()` (first-stint bonus derived from `start_lap > 0`)
- [ ] `calculate_remaining()` method on `StrategyEngine`
- [ ] FIA compound diversity check against already-used compounds
- [ ] Recalculation triggers in `live_race.py` (pit events, SC changes, `try/finally`)
- [ ] Strategy panel added to `LiveDashboard.tsx`
- [ ] Tests: mid-race 1-stop remaining, compound diversity already satisfied

---

## Edge Cases and Error Handling

| Scenario | Handling |
|----------|----------|
| No race active | Show "No live session available" with next race date |
| User joins mid-race | Full state snapshot on SSE connect (every message is a snapshot) |
| SSE connection drops | `EventSource` auto-reconnects; next message is full state |
| Driver DNF | If OpenF1 stops returning data for a driver, show "No data" |
| OpenF1 rate limit (429) | Respect `Retry-After`, backoff 30s → 60s → show "Data source unavailable" |
| OpenF1 auth error (401/403) | Stop polling immediately, show "OpenF1 API key invalid" |
| Strategy recalc in progress | Show "recalculating..." with stale data; `needs_recalc` flag queues the next run |
| Unknown compound after pit | Show previous compound until next poll confirms (~8s) |
| OpenF1 down (500/timeout) | Log warning, keep last known state, show "Data may be delayed" |
| Server restart mid-race | State is lost. Next poll cycle rebuilds current positions/compounds within ~10s. Historical race control messages are not recovered. |
| Formation lap | Ignore laps before lap 1; `current_lap` starts at 1 |
| No SSE clients for 5 min | Stop polling to avoid wasting API quota |
| Quiet period (long SC) | SSE keepalive comment every 15s prevents connection drops |

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| OpenF1 tier | Sponsor (€9.90/month) | 60 req/min allows comfortable polling headroom |
| Uvicorn workers | 1 (not 2) | Module-level state requires single-process |
| SSE event types | 1 (full snapshot) | Eliminates partial update bugs; state is small |
| SSE keepalive | `: keepalive` every 15s | Prevents proxy/browser drops during quiet periods |
| SSE reconnection | Ignore `Last-Event-ID` | Every message is a full snapshot — no tracking needed |
| nginx timeout | 300s (not 9000s) | Let EventSource auto-reconnect; matches existing API config |
| State machine | No (`is_safety_car` bool) | Race control messages are already human-readable |
| Polling intervals | Single ~8s round-robin | Simpler than priority-based staggering; tune with real data |
| Polling lifecycle | POST to start, auto-stop on race end or no clients | Explicit start avoids unwanted API usage |
| Session key | Query OpenF1 `/v1/sessions` | Maps year + country to integer session_key |
| OpenF1 client | Module functions, not a class | `fetch_openf1(params: dict)` + shared `httpx.AsyncClient` |
| `httpx` lifecycle | FastAPI shutdown handler | Closes connection pool cleanly on process exit |
| Data model | Plain dicts | Matches existing codebase convention |
| Frontend types | Add to existing `types.ts` | Don't fragment type definitions into a second file |
| Frontend components | 2 new files (`useLiveRace.ts`, `LiveDashboard.tsx`) | Inline small pieces, no separate component files |
| `skip_first_stint_bonus` | Derived from `start_lap > 0` | 2 params on `_simulate_race()` instead of 3 |
| Recalc scope | All drivers, frontend filters | Backend doesn't need to know which team the user selected |
| Recalc safety | `try/finally` on `is_calculating` | Prevents stuck flag from freezing all future recalculations |
| Sprint races | Deferred to v2 | Not just tyre rules — different session length, no Q2 rule, edge cases |
| Replay mode | Deferred to v2 | Test with fixtures and historical session_keys; full replay is a separate feature |
| SC pit cost reduction | Deferred to v2 | Directionally correct without it |
| Red flag free tyres | Deferred to v2 | Too rare for v1 |
| Gap chart | Deferred to v2 | Driver cards show current gap already |
| `_simulate_race()` refactor | No — add parameters instead | Don't touch validated 160-line simulation loop |

## Deferred to v2

- **Historical replay mode** (time-delayed playback with speed controls)
- **Sprint race support** (tyre rules, shorter session handling)
- **Gap chart** (gap-to-leader line chart over laps)
- **SC pit cost reduction** (~15s cheaper pit under safety car)
- **Red flag free tyre changes** (disable `_MAX_SETS_PER_COMPOUND`)
- **Manual recalculation endpoint** (`POST /api/live/strategy`)

## Dependencies

**Backend (add to requirements.txt):**
```
httpx>=0.27           # Async HTTP client for OpenF1 polling
sse-starlette>=2.0    # SSE support for FastAPI
```

**Frontend:** No new dependencies. Uses React 19 built-in `useSyncExternalStore`.

## Files Summary

| Phase | New Files | Modified Files |
|-------|-----------|----------------|
| 1 | `live_race.py`, `test_live_race.py`, `fixtures/openf1/`, `useLiveRace.ts`, `LiveDashboard.tsx` | `api.py`, `requirements.txt`, `nginx.conf`, `start.sh`, `App.tsx`, `api.ts`, `types.ts` |
| 2 | — | `strategy.py`, `test_strategy.py`, `live_race.py`, `LiveDashboard.tsx` |

**Total new files: 5** (was 6). **Total estimated LOC: ~800-1,200** (was ~1,000-1,500).

## Verification

After each phase:
1. `PYTHONPATH=backend backend/venv/bin/pytest backend/tests/ -v -s` — all tests pass
2. Phase 1: Point at a historical session_key, verify SSE streams state snapshots, frontend renders driver data
3. Phase 2: Verify `calculate_remaining()` produces valid strategies for mid-race scenarios; strategy panel shows recommendations
4. End-to-end: `docker build -t f1-strat .` and verify SSE flows through nginx with `--workers 1`
