# Multi-stage Dockerfile for the F1 Strategy Tool
# Stage 1: Build the React frontend
# Stage 2: Runtime with Python (FastAPI) + nginx (reverse proxy)

# ── Stage 1: Build React frontend ───────────────────────────────────
# Use Debian-based image (not Alpine) because @tailwindcss/oxide requires
# platform-specific native bindings.  Alpine uses musl libc and Deno's npm
# compatibility doesn't reliably install musl optional dependencies.
# Debian with glibc resolves the correct -linux-*-gnu bindings automatically.
FROM denoland/deno:debian AS frontend-build

WORKDIR /build

# Copy dependency manifests first — Docker caches the install layer
# so it only re-runs when dependencies change, not on every code edit.
# The lockfile is excluded because it may contain platform-specific
# resolutions (e.g. macOS-arm64 native bindings for @tailwindcss/oxide)
# that won't work on Alpine Linux (musl).  Deno regenerates the lockfile
# on install with the correct platform bindings.
COPY frontend/package.json frontend/deno.json ./
RUN deno install --frozen=false

# Now copy the rest of the frontend source and build it.
# --frozen=false allows the build to proceed without a matching lockfile
# since we regenerated it in the install step above.
COPY frontend/ ./
RUN deno task build

# ── Stage 2: Runtime ────────────────────────────────────────────────
# Python 3.12 (not 3.14) — best wheel support for numpy/scipy/pandas
FROM python:3.12-slim

# Upgrade base packages to pick up security patches (e.g. CVE-2026-0861
# in libc6/libc-bin), install nginx, then clean up apt cache.
RUN apt-get update && \
    apt-get upgrade -y && \
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

# OpenF1 sponsor tier credentials (optional).  When set, the backend
# exchanges these for a bearer token to access real-time race data.
# Without them, everything still works via the free tier (historical only).
#
#   docker run -e OPENF1_USERNAME=your_user -e OPENF1_PASSWORD=your_pass ...
#
# The env vars are read at runtime by live_race.py — no build-time changes needed.

EXPOSE 80

CMD ["/app/start.sh"]
