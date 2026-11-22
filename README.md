# NASA NeoWs Data Pipeline

A robust, containerized, and idempotent data pipeline that fetches Near Earth Object (NeoWs) data from NASA's public API, validates it via Pydantic models, and safely ingests/upserts records into a PostgreSQL database.

## Why NASA NeoWs?

NASA’s Near Earth Object (NeoWs) API is an absolute goldmine for building unique, visually stunning, and technically challenging engineering projects. Unlike standard financial or weather datasets, space data brings real-world stakes, massive physical scales, and rich multi-dimensional metrics straight to my software.

By combining this API with a robust data pipeline and automated storage layer, I can unlock endless possibilities:
- **Rich Cosmic Attributes:** Every record includes real-world telemetry—such as relative velocity, precise physical dimensions, miss distances measured in Lunar Distances (LD), and critical hazard classification flags.
- **Full-Stack Extensibility:** Move effortlessly from backend data engineering (idempotent PostgreSQL upserts, rate-limit resilience, semantic exit codes) to frontend visualization (sci-fi planetary defense control rooms, risk-matrix analytics, and 3D orbital flight paths).
- **High-Impact Portfolio Piece:** It bridges the gap between raw data ingestion and compelling consumer or B2B applications, demonstrating end-to-end system design capability.

---

## Tech Stack
* **Language:** Python 3.14 (`requests`, `pydantic`, `psycopg`, `python-dotenv`)
* **Database:** PostgreSQL 15 (Dockerized), schema managed with [dbmate](https://github.com/amacneil/dbmate) migrations
* **Testing:** `pytest`, `responses`
* **Dashboard:** Streamlit (`app.py`)
* **Infrastructure:** Docker Compose

---

## Quick Start Guide

### 1. Clone & Configure Environment
Clone the repository and copy the example environment file:

```bash
cp .env.example .env
```

Fill out your `.env` with your PostgreSQL credentials and your free NASA API key from <https://api.nasa.gov>. `DEMO_KEY` works but is limited to a few requests per hour.

### 2. Start PostgreSQL via Docker
Ensure Docker Desktop is running, then start the database:

```bash
docker compose up -d
```

This starts PostgreSQL and runs the one-shot `migrate` service, which applies any pending migrations in `db/migrations/` (see [Database Migrations](#database-migrations)).

### 3. Set Up Virtual Environment & Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt        # add -dev to also install test tools
```

### 4. Run the Pipeline
Fetch and upsert asteroid data for today:

```bash
scripts/run_pipeline.sh
```

This applies any pending migrations first, then runs `pipeline.py`. Arguments are passed through, so you can give a custom date range (any length; it is fetched in 7-day windows, NASA's limit):

```bash
scripts/run_pipeline.sh --start 2026-11-10 --end 2026-11-15
scripts/run_pipeline.sh --start 2026-11-10 --dry-run   # fetch + validate only, no database writes
```

The schema step and the data step are separate programs, and `run_pipeline.sh` is the only place that combines them:

| Piece | Job | Knows about |
|---|---|---|
| `scripts/migrate.sh apply\|check` | Bring the schema up to date, or check that it is | Docker, dbmate |
| `pipeline.py` | Fetch, validate, upsert | A database connection, not migrations |
| `scripts/run_pipeline.sh` | Run the schema step, then the data step | The order of the two steps |
| `scripts/daily_pipeline.sh` | Wait for the network and Docker, then call `run_pipeline.sh` | Mac sleep/wake |

`MIGRATIONS=check scripts/run_pipeline.sh` never changes the schema and refuses to run on an outdated one. `MIGRATIONS=skip` leaves out the schema step entirely, for example for a `--dry-run` without Docker. `python pipeline.py ...` still works on its own, but it doesn't apply migrations.

`--start`/`--end` are short forms of `--start-date`/`--end-date`. Re-running any range is safe: unchanged rows are left alone, and an older close approach never overwrites a newer one.

#### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Configuration error: missing `.env` settings, or NASA rejected the API key (HTTP 401/403). Retrying won't help |
| 2 | Invalid command-line arguments |
| 3 | NASA API unavailable or rate limited; try again later |
| 4 | Database error (from `migrate.sh`: Docker not running or PostgreSQL not healthy) |
| 5 | Partial run: some 7-day windows were saved before a later one failed; re-run the same range to finish |
| 6 | Schema step failed: a migration errored, or (`MIGRATIONS=check`) migrations are pending. From `run_pipeline.sh` / `migrate.sh` only |
| 7 | NASA rejected the request (HTTP 4xx other than 401/403/429) or answered in an unexpected format (API schema changed); retrying won't help, the code needs updating |
| 130 | Interrupted (Ctrl+C) |

### 5. Open the Dashboard (optional)

```bash
venv/bin/streamlit run app.py   # or `streamlit run app.py` with venv activated
```

Use the same `venv` as the pipeline (`scripts/run_pipeline.sh` runs `venv/bin/python`), so the dashboard and the pipeline always run the same library versions.

It shows totals over the whole `asteroids` table (computed in SQL) and lists the newest 5,000 rows. If the database is unavailable or empty it falls back to the live NASA API (last 3 days, one quick retry, so the page never waits minutes on a rate limit).

Failures are not cached, so the page recovers as soon as a source is back, but a source that just failed is skipped for a cooldown (database 30 s, NASA 120 s) so that clicking around a degraded page stays instant. **Refresh data** clears the caches and the cooldowns. The Diagnostics panel in the sidebar says why a source was skipped.

---

## Code Layout

| Module | Layer | Job |
|---|---|---|
| `pipeline.py` | Entry point | CLI arguments, builds the dependencies, maps errors to exit codes |
| `app.py` | Entry point | Dashboard wiring (sources, caches, circuit breakers) and Streamlit rendering |
| `ui_messages.py` | Entry point | How the dashboard words each error (`UI_MESSAGES`) |
| `ingest.py` | Application service | `IngestService` (fetch, validate, store window by window), `RunObserver`s such as `LogObserver`, `DryRunStore` |
| `dashboard_data.py` | Application service | Builds the dashboard's `AsteroidView` from a store or a feed; `FallbackReader` |
| `resilience.py` | Application service | `CircuitBreaker` for interactive callers |
| `ports.py` | Interfaces | `FeedSource`, `AsteroidStore`, `AsteroidReader` protocols; they mention only domain types |
| `domain.py` | Domain | `Asteroid`, `CloseApproach`, `Observation`, the `latest()` rule, `FeedBatch`, `date_windows`, read models |
| `errors.py` | Domain | The `NeoError` hierarchy (classification only) |
| `config.py` | Infrastructure | `Settings`: the only code that reads `.env` / the environment |
| `client.py` | Adapter | NASA HTTP client, retry profiles, HTTP errors to typed errors |
| `models.py` | Adapter | NASA JSON shapes, validation, mapping to `Observation` |
| `database.py` | Adapter | PostgreSQL connection and `AsteroidRepository` (write: `save`; read: `overview`, `list_recent`) |

Adapters translate library exceptions (`requests`, `pydantic`, `psycopg`) into `NeoError` subclasses. Entry points catch only `NeoError`; anything else is a bug and crashes with a traceback instead of being reported as an outage. How an error is presented is decided by each entry point: `pipeline.CLI_OUTCOMES` (exit code and log label) and `ui_messages.UI_MESSAGES` (dashboard text).

Which close approach is kept is decided in one place, `domain.latest()`. The parser keeps every approach it sees, and the repository stores the newest per asteroid, so keeping the full history later only needs a new table and a second write.

---

## 🧪 Running Tests

```bash
pip install -r requirements-dev.txt
pyright     # type-check; enforces that adapters and test fakes match ports.py
pytest
```

* `tests/test_domain.py`: the `latest()` rule, date windows, strict entities.
* `tests/test_models.py`: Pydantic validation of NeoWs payloads and mapping to observations.
* `tests/test_client.py`: the HTTP client, with NASA simulated by `responses` (no real API calls): errors, retries, timeouts, rate limits, retry profiles.
* `tests/test_config.py`: settings validation.
* `tests/test_database.py`: the idempotent upsert, totals and the dashboard query against PostgreSQL.
* `tests/test_ingest.py`: `IngestService` and its observers with in-memory fakes.
* `tests/test_dashboard_data.py`: the dashboard's views and fallback, without Streamlit.
* `tests/test_resilience.py`: the circuit breaker and the dashboard's error wording.
* `tests/test_ports.py`: protocol conformance (checked by pyright).
* `tests/test_pipeline.py`: argument parsing and exit codes end to end.

Database tests use the PostgreSQL from `.env` but never touch your data: they build a throwaway schema from `db/migrations/`, roll back every test, and drop the schema afterwards. If PostgreSQL isn't running, they are skipped.

---

## Database Migrations

The schema lives in versioned SQL files in `db/migrations/`. dbmate records applied files in a `schema_migrations` table and only runs new ones, so schema changes reach existing databases without wiping data.

```bash
docker compose run --rm migrate status            # what's applied / pending
docker compose run --rm migrate new add_something # creates db/migrations/<timestamp>_add_something.sql
docker compose run --rm migrate up                # apply pending migrations
```

Each file has a `-- migrate:up` section and a `-- migrate:down` section. Rules:

* Never edit a migration that has already been applied; add a new one.
* If you change a `NUMERIC` column, update the matching limit in `models.py`. A test fails if they drift apart.
* Prefer changes that don't lock or rewrite the table: add nullable columns, use `CREATE INDEX CONCURRENTLY` (with `-- migrate:up transaction:false`), and add `NOT NULL` via a `CHECK ... NOT VALID` constraint that is validated afterwards.
* Put only **one** `CONCURRENTLY` statement in a `transaction:false` file. dbmate sends the whole section at once, and Postgres runs multiple statements as a single transaction, which `CONCURRENTLY` rejects. Two indexes means two files.
* Files are applied in timestamp order, and `DBMATE_STRICT` rejects a file older than one already applied. If that happens after a merge, rename your file to a new timestamp (`migrate new` gives you one).

### CI

[.github/workflows/migrations.yml](.github/workflows/migrations.yml) runs on every push and PR. On a fresh PostgreSQL it:

1. applies the base branch's migrations, then the PR's with `--strict`, which fails if the PR adds a migration that sorts before one already on the base branch;
2. rolls every migration back and applies them again, which checks that each `-- migrate:down` really undoes its `up`;
3. type-checks the code with pyright and runs the test suite against the migrated database.
