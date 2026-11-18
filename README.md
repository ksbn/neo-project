# NASA NeoWs Data Pipeline

A robust, containerized, and idempotent data pipeline that fetches Near Earth Object (NeoWs) data from NASA's public API, validates it via Pydantic models, and safely ingests/upserts records into a PostgreSQL database.

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
python pipeline.py
```

Or specify a custom date range (any length; it is fetched in 7-day windows, NASA's limit):

```bash
python pipeline.py --start 2026-11-10 --end 2026-11-15
python pipeline.py --start 2026-11-10 --dry-run   # fetch + validate only, no database writes
```

`--start`/`--end` are short forms of `--start-date`/`--end-date`. Re-running any range is safe: unchanged rows are left alone, and an older close approach never overwrites a newer one.

#### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Configuration error (e.g. missing `.env` settings) |
| 2 | Invalid command-line arguments |
| 3 | NASA API unavailable or returned unusable data; try again later |
| 4 | Database error |
| 5 | Partial run: some 7-day windows were saved before a later one failed; re-run the same range to finish |
| 130 | Interrupted (Ctrl+C) |

### 5. Open the Dashboard (optional)

```bash
streamlit run app.py
```

It reads from PostgreSQL, and falls back to the live NASA API if the database is unavailable.

---

## 🧪 Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

* `tests/test_models.py`: Pydantic validation of NeoWs payloads.
* `tests/test_client.py`: the HTTP client, with NASA simulated by `responses` (no real API calls): errors, retries, timeouts, rate limits.
* `tests/test_database.py`: the idempotent upsert against PostgreSQL.
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
