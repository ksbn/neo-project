#!/bin/bash
# Daily job: make sure PostgreSQL is up, then load the last 3 days of NeoWs data.
#
# The 3-day window catches up on days missed while the Mac was off; re-loading
# days already stored is safe because the upsert is idempotent.
# Scheduled by ~/Library/LaunchAgents/com.neo-project.daily-pipeline.plist
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$PROJECT_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') daily_pipeline: $*"; }

# After waking from sleep the network can take a while to come back.
# Wait up to 10 minutes for NASA to be reachable instead of failing immediately.
for _ in $(seq 1 40); do
    curl -s -o /dev/null --max-time 10 https://api.nasa.gov && break
    sleep 15
done
if ! curl -s -o /dev/null --max-time 10 https://api.nasa.gov; then
    log "api.nasa.gov is still unreachable after 10 minutes (no internet?); will try at the next scheduled run"
    exit 3
fi

# Start Docker Desktop if it isn't running, and wait up to 3 minutes for it.
if ! docker info >/dev/null 2>&1; then
    log "Docker is not running; starting Docker Desktop"
    open -a Docker
    for _ in $(seq 1 36); do
        docker info >/dev/null 2>&1 && break
        sleep 5
    done
    if ! docker info >/dev/null 2>&1; then
        log "Docker did not start within 3 minutes; giving up"
        exit 1
    fi
fi

# Start Postgres if needed, then apply any pending migrations. (Not a single
# `up --wait`: it reports failure when the one-shot migrate container exits.)
docker compose up -d --wait db
docker compose run --rm migrate up

start_date="$(date -v-2d +%F)"
end_date="$(date +%F)"
log "Running pipeline for $start_date to $end_date"
exec "$PROJECT_DIR/venv/bin/python" pipeline.py --start "$start_date" --end "$end_date"
