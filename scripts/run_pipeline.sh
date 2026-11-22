#!/bin/bash
# Composition only: schema step, then data step. Neither step knows about the
# other; this file is the one place that decides their order.
#
#   scripts/run_pipeline.sh [planetary_defense.cli args...]
#   scripts/run_pipeline.sh --start 2026-11-10 --end 2026-11-15
#   MIGRATIONS=check scripts/run_pipeline.sh   # never change the schema; refuse to run if it's outdated
#   MIGRATIONS=skip  scripts/run_pipeline.sh   # no schema step at all (e.g. --dry-run without Docker)
#
# Exit code: the schema step's code if it fails (see migrate.sh), otherwise
# the CLI's own code (see README "Exit codes").
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

MIGRATIONS="${MIGRATIONS:-apply}"
case "$MIGRATIONS" in
    apply|check) "$PROJECT_DIR/scripts/migrate.sh" "$MIGRATIONS" || exit $? ;;
    skip) ;;
    *) echo "MIGRATIONS must be apply, check or skip (got '$MIGRATIONS')" >&2; exit 2 ;;
esac

PYTHON="$PROJECT_DIR/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
# The package lives in src/; putting it on the path means the scheduled job
# works even if `pip install -e .` was never run in this venv.
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m planetary_defense.cli "$@"
