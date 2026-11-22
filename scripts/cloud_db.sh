#!/bin/bash
# TEMPORARY: the hosted PostgreSQL behind the Streamlit Community Cloud demo.
# Delete together with the deployment (see README, "Temporary Streamlit Cloud deployment").
#
#   scripts/cloud_db.sh migrate          # apply db/migrations to the hosted database (dbmate)
#   scripts/cloud_db.sh copy             # copy all data from the local database into it (must be empty)
#   scripts/cloud_db.sh copy --replace   # empty the hosted tables first, then copy
#   scripts/cloud_db.sh status           # row counts in the hosted database
#
# Connection settings come from .env.cloud (git-ignored; template: .env.cloud.example).
# The password is passed to the containers through the environment, never on a command line.
set -euo pipefail

cd "$(dirname "$0")/.."
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') cloud_db: $*" >&2; }

[ -f .env.cloud ] || { log "missing .env.cloud (copy .env.cloud.example and fill it in)"; exit 1; }
set -a; source .env.cloud; set +a
: "${DB_HOST:?}" "${DB_NAME:?}" "${DB_USER:?}" "${DB_PASSWORD:?}"
DB_PORT="${DB_PORT:-5432}"
DB_SSLMODE="${DB_SSLMODE:-require}"

DBMATE_IMAGE="ghcr.io/amacneil/dbmate:2.37.0"
PSQL_IMAGE="postgres:15"
TABLES="watchlist, idempotency_keys, anomaly_alerts, orbit_approaches, orbital_elements, close_approaches, ingestion_runs, asteroids"

cloud_psql() {  # psql against the hosted database; extra args are passed through
    docker run --rm -i -e PGPASSWORD="$DB_PASSWORD" -e PGSSLMODE="$DB_SSLMODE" "$PSQL_IMAGE" \
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q "$@"
}

case "${1:-}" in
    migrate)
        # DATABASE_URL with the password URL-encoded; built inside the container's environment only.
        url="$(DB_PASSWORD="$DB_PASSWORD" python3 -c 'import os, urllib.parse as u; print(u.quote(os.environ["DB_PASSWORD"], safe=""))')"
        DATABASE_URL="postgres://$DB_USER:$url@$DB_HOST:$DB_PORT/$DB_NAME?sslmode=$DB_SSLMODE" \
            docker run --rm -e DATABASE_URL -v "$PWD/db/migrations:/db/migrations" "$DBMATE_IMAGE" \
            --no-dump-schema -d /db/migrations up --strict
        ;;
    copy)
        rows="$(cloud_psql -At -c "SELECT COUNT(*) FROM asteroids")"
        if [ "$rows" != "0" ]; then
            if [ "${2:-}" != "--replace" ]; then
                log "the hosted database already holds $rows asteroids; use 'copy --replace' to overwrite them"
                exit 1
            fi
            log "emptying the hosted tables"
            cloud_psql -c "TRUNCATE $TABLES RESTART IDENTITY"
        fi
        local_user="$(docker exec neo_postgres printenv POSTGRES_USER)"
        local_db="$(docker exec neo_postgres printenv POSTGRES_DB)"
        log "copying data from the local $local_db to $DB_HOST/$DB_NAME"
        # Data only (the schema comes from the migrations), in foreign-key order, without dbmate's bookkeeping.
        docker exec neo_postgres pg_dump -U "$local_user" -d "$local_db" --data-only --no-owner \
            --exclude-table=schema_migrations | cloud_psql
        "$0" status
        ;;
    status)
        cloud_psql -c "SELECT 'asteroids' AS table, COUNT(*) FROM asteroids
            UNION ALL SELECT 'close_approaches', COUNT(*) FROM close_approaches
            UNION ALL SELECT 'anomaly_alerts', COUNT(*) FROM anomaly_alerts
            UNION ALL SELECT 'orbital_elements', COUNT(*) FROM orbital_elements
            UNION ALL SELECT 'orbit_approaches', COUNT(*) FROM orbit_approaches
            UNION ALL SELECT 'ingestion_runs', COUNT(*) FROM ingestion_runs
            UNION ALL SELECT 'watchlist', COUNT(*) FROM watchlist"
        ;;
    *)
        echo "usage: $0 migrate | copy [--replace] | status" >&2
        exit 2
        ;;
esac
