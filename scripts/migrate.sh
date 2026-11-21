#!/bin/bash
# Schema step, and nothing else: bring the database schema up to date, or
# check that it already is. Knows about dbmate and Docker; knows nothing about
# the pipeline.
#
#   scripts/migrate.sh apply   # start Postgres if needed, apply pending migrations
#   scripts/migrate.sh check   # change nothing; fail if any migration is pending
#
# Exit codes:
#   0  schema is up to date
#   2  bad usage
#   4  database could not be started or reached
#   6  migration failed (apply) or migrations are pending (check)
set -uo pipefail

cd "$(dirname "$0")/.."
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') migrate: $*" >&2; }

mode="${1:-}"
case "$mode" in
    apply|check) ;;
    *) echo "usage: $0 apply|check" >&2; exit 2 ;;
esac

if ! docker info >/dev/null 2>&1; then
    log "Docker is not running; start Docker Desktop and try again"
    exit 4
fi

# Not part of the migrate command itself: `up --wait` would report failure
# when the one-shot migrate container exits.
if ! docker compose up -d --wait db; then
    log "PostgreSQL did not become healthy"
    exit 4
fi

if [ "$mode" = apply ]; then
    if ! docker compose run --rm migrate up; then
        log "applying migrations failed; see the dbmate error above"
        exit 6
    fi
else
    if ! docker compose run --rm migrate status --exit-code; then
        log "schema is out of date; run: scripts/migrate.sh apply"
        exit 6
    fi
fi
