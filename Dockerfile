# Multi-stage Dockerfile for the F1 Strategy Tool
# Stage 1: Build the React frontend
# Stage 2: Runtime with Python (FastAPI) + nginx (reverse proxy)

# ── Stage 1: Build React frontend ───────────────────────────────────
FROM denoland/deno:alpine AS frontend-build

WORKDIR /build

# Copy dependency manifests first — Docker caches the install layer
# so it only re-runs when dependencies change, not on every code edit
COPY frontend/package.json frontend/deno.json frontend/deno.lock ./
RUN deno install

# Now copy the rest of the frontend source and build it
COPY frontend/ ./
RUN deno task build

# ── Stage 2: Runtime ────────────────────────────────────────────────
# Python 3.12 (not 3.14) — best wheel support for numpy/scipy/pandas
FROM python:3.12-slim

# Install nginx and clean up apt cache to keep image smaller
RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cached unless requirements.txt changes)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy the backend source code
COPY backend/f1_strat/ /app/backend/f1_strat/

# Copy the built React app from stage 1 into nginx's serving directory
COPY --from=frontend-build /build/dist/ /usr/share/nginx/html/

# Remove the default nginx config and use ours
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/f1-strat.conf

# Copy the startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# FastF1 cache — persisted as a Docker volume so race data survives
# container restarts. The path matches what cache.py resolves to:
# Path(__file__).resolve().parent.parent / ".fastf1_cache"
#   → /app/backend/f1_strat/../.fastf1_cache
#   → /app/backend/.fastf1_cache
VOLUME /app/backend/.fastf1_cache

EXPOSE 80

CMD ["/app/start.sh"]
