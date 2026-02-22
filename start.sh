#!/usr/bin/env bash
# Start script for the F1 strategy tool container.
# Runs uvicorn (FastAPI) and nginx side-by-side.
# If either process exits, the container stops — this is what
# Kubernetes expects (crash → restart via the pod spec).

set -euo pipefail

# Clean shutdown: forward SIGTERM/SIGINT to child processes so they
# get a chance to close connections gracefully
cleanup() {
    echo "Shutting down..."
    kill "$UVICORN_PID" 2>/dev/null || true
    kill "$NGINX_PID" 2>/dev/null || true
    wait
}
trap cleanup SIGTERM SIGINT

# Start the FastAPI backend with 1 worker.  The live race tracking
# feature uses module-level state (a dict shared between the polling
# task and SSE endpoints).  With 2+ workers, each worker is a separate
# process with its own copy of the state — the polling loop would run
# in one worker but SSE requests might land on another (with empty state).
# Single worker is fine for a fan tool on a home Kubernetes lab.
PYTHONPATH=/app/backend uvicorn f1_strat.api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 &
UVICORN_PID=$!

# Start nginx in the foreground (daemon off) so Docker sees it as
# the main process and container logs capture its output
nginx -g "daemon off;" &
NGINX_PID=$!

# Wait for either process to exit — if one crashes, we want the
# container to stop so Kubernetes can restart it
wait -n
echo "A process exited unexpectedly, stopping container..."
cleanup
exit 1
