# Security Profile — F1 Race Strategy Tool

## Project classification

This is a **single-user fan tool** running on a home Kubernetes lab. It has no user accounts, no authentication, no database, and stores no personal data. The risk profile is low — the worst case for a full compromise is loss of the OpenF1 API credentials and temporary disruption of the tool.

## Attack surface

### Inputs from untrusted sources

| Input | Where | Validation |
|-------|-------|------------|
| URL path parameters (`year`, `grand_prix`) | `api.py` endpoints | FastAPI type validation (int, str). Passed to FastF1/OpenF1 which handle their own escaping. No SQL, no shell execution. |
| Query parameters (`race_laps`, `pit_stop_loss`, etc.) | `api.py` endpoints | FastAPI `Query()` with `ge`/`le` bounds. Numeric types enforced by Pydantic. |
| POST body (`StrategyRequest`) | `/api/strategy` POST | Pydantic model with typed fields and bounds. `weather_windows` is a list of validated models. |
| SSE `session_key` | `/api/live/stream/{session_key}` | Integer type check. Compared against known session key before streaming. |

**No user-generated content is stored.** All data flows from external APIs (FastF1, OpenF1) through the backend to the frontend. There is no database, no file uploads, no user accounts.

### Network exposure

| Port | Service | Access |
|------|---------|--------|
| 80 (container) / 3000 (host) | nginx | Serves static React build + reverse-proxies `/api/*` to uvicorn |
| 8000 (internal only) | uvicorn | Only reachable from nginx within the container. Not exposed to the host. |

In the Docker deployment, only port 80 is exposed. Uvicorn binds to `0.0.0.0:8000` inside the container but is not published to the host — nginx is the sole entry point.

### CORS

CORS is set to `allow_origins=["*"]` in `api.py`. This is acceptable because:
- The API serves only public F1 data (no private or user-specific data)
- There is no authentication to protect via same-origin policy
- The tool runs on a private home network

If the tool is ever exposed to the public internet, restrict `allow_origins` to the frontend's domain.

## Credentials

### OpenF1 API credentials

The only secrets in this project are the optional OpenF1 sponsor-tier credentials:

- **Storage:** Environment variables `OPENF1_USERNAME` and `OPENF1_PASSWORD`
- **Usage:** Exchanged for a bearer token via `POST https://api.openf1.org/token` in `live_race.py`
- **Token lifetime:** 1 hour, refreshed automatically 60 seconds before expiry
- **Token storage:** In-memory only (`_token_value` module variable). Never written to disk, never logged, never included in SSE state snapshots.
- **Fallback:** When credentials are not set, the app uses OpenF1's free tier (historical data only, lower rate limits). No functionality is lost except live race tracking latency.

**Never commit credentials to the repository.** Pass them at container runtime:

```bash
docker run -e OPENF1_USERNAME=... -e OPENF1_PASSWORD=... ...
```

Or use Kubernetes secrets mounted as environment variables.

### FastF1 API

FastF1 accesses the public F1 live timing API. No credentials are needed. Data is cached locally to `backend/.fastf1_cache/` (a Docker volume). The cache contains only public F1 session data — no secrets.

## Common vulnerability classes

### SQL injection — Not applicable

No database. All data comes from in-memory computation and external API calls.

### XSS — Low risk

The backend returns JSON data (lap times, strategy results). The React frontend renders this data. No user-generated HTML or content is stored or reflected. Standard React JSX escaping applies.

### Command injection — Not applicable

No shell commands are executed from user input. All processing is pure Python computation (numpy, pandas, FastF1).

### SSRF — Low risk

The backend makes outbound HTTP requests to two fixed hosts:
- F1 live timing API (via FastF1 library)
- `api.openf1.org` (via httpx in `live_race.py`)

No user-controlled URLs are fetched. The `grand_prix` parameter is passed as a query parameter to OpenF1 (not as a URL path), so it cannot redirect requests.

### Denial of service — Moderate risk

- **CPU-bound:** The strategy engine brute-forces all valid pit lap combinations. A 3-stop race with 78 laps generates ~300K combinations. This is bounded by `max_stops` (capped at 3) and `race_laps` (capped at 10-100 by FastAPI `Query(ge=10, le=100)` on both GET and POST endpoints). Worst case: ~3 seconds of CPU.
- **Memory:** In-memory caches (`_analysis_cache`, `_base_lap_cache`) grow with the number of unique (year, GP) combinations requested. A full season is ~24 entries — negligible. No cache eviction is needed at this scale.
- **SSE connections:** Each connected client holds an async generator. The `_sse_client_count` is tracked but not capped. On a home lab with 1-2 clients this is fine. If exposed publicly, add a connection limit.
- **External API rate limits:** FastF1 has a 500 calls/hour limit. OpenF1 free tier has lower limits. The app respects `429 Retry-After` headers and backs off progressively (`_backoff_seconds` in `live_race.py`).

## Dependencies

Key dependencies and their trust level:

| Dependency | Purpose | Trust |
|------------|---------|-------|
| FastF1 | F1 session data loading and caching | Well-maintained open source, 3K+ GitHub stars |
| FastAPI | Web framework | Industry standard, actively maintained |
| httpx | Async HTTP client for OpenF1 | Widely used, from the encode team |
| numpy, pandas, scipy | Data processing | Core scientific Python ecosystem |
| sse-starlette | Server-Sent Events | Small library, minimal attack surface |
| React, Vite | Frontend | Industry standard |

Pin versions in `requirements.txt` and `package.json`. Run `pip audit` or `safety check` periodically against the Python dependencies.

## Reporting vulnerabilities

This is a personal project. If you find a security issue, open a GitHub issue or contact the repository owner directly.
