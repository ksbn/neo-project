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
* **Dashboard:** Streamlit (`src/planetary_defense/ui/app.py`, started through the root `app.py`)
* **Packaging & tooling:** `pyproject.toml` (setuptools), `ruff`, `pyright`
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
pip install -e ".[dev]"                # the package (editable) plus test, lint and type-check tools
```

### 4. Run the Pipeline
Fetch and upsert asteroid data for today:

```bash
scripts/run_pipeline.sh
```

This applies any pending migrations first, then runs the CLI (`python -m planetary_defense.cli`). Arguments are passed through, so you can give a custom date range (any length; it is fetched in 7-day windows, NASA's limit):

```bash
scripts/run_pipeline.sh --start 2026-11-10 --end 2026-11-15
scripts/run_pipeline.sh --start 2026-11-10 --dry-run   # fetch + validate only, no database writes
```

The schema step and the data step are separate programs, and `run_pipeline.sh` is the only place that combines them:

| Piece | Job | Knows about |
|---|---|---|
| `scripts/migrate.sh apply\|check` | Bring the schema up to date, or check that it is | Docker, dbmate |
| `planetary_defense.cli` | Fetch, validate, upsert | A database connection, not migrations |
| `scripts/run_pipeline.sh` | Run the schema step, then the data step | The order of the two steps |
| `scripts/daily_pipeline.sh` | Wait for the network and Docker, then call `run_pipeline.sh` | Mac sleep/wake |

`MIGRATIONS=check scripts/run_pipeline.sh` never changes the schema and refuses to run on an outdated one. `MIGRATIONS=skip` leaves out the schema step entirely, for example for a `--dry-run` without Docker. `neo-pipeline ...` (or `python -m planetary_defense.cli ...`) runs the data step on its own, without applying migrations.

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
venv/bin/streamlit run app.py --server.port 8502   # or `streamlit run app.py` with venv activated
```

Use the same `venv` as the pipeline (`scripts/run_pipeline.sh` runs `venv/bin/python`), so the dashboard and the pipeline always run the same library versions.

It opens **ASTRAEA: Planetary Defense Command**, a dark control-room layout (theme in `.streamlit/config.toml` and `ui/theme.py`):

* **Command header:** title, an operator / UTC time / feed status pill, and **Sync / Refresh Feed**, which runs the pipeline for the last 3 days with your `NASA_API_KEY` and reloads. If it fails, a non-blocking banner says why and the stored data stays on screen.
* **KPI strip:** **Threat Level** (worst alert on passes within ±7 days, recorded or predicted: LOW, GUARDED, ELEVATED, CRITICAL), **Near-Earth Objects** (tracked count with a 14-day approaches-per-day sparkline), **Close Approaches** in a ±3-day window with the closest pass, and **Global Defense Readiness** (NOMINAL, DEGRADED, IMPAIRED, STANDBY, with the last sync). All from stored data.
* **Left column:** **Hazard Alerts** (severity-badged cards with pass time, size and reason; **Track** selects the asteroid) and **Close Approach Data** (Object Name, Date (UTC), Distance (LD) coloured by tier: under 1 LD critical, 1–5 LD warning, Velocity, Diameter (m)). Selecting a row tracks it.
* **Right column:** the **3D Space Viewport** for the tracked asteroid and its **Orbital Telemetry Readouts** (object and ID, inclination, eccentricity, semi-major axis, H, diameter, minimum Earth distance in LD and km, Earth MOID, orbit class, and the next predicted Earth pass) with Live Feed / Data Synced / Orbit status indicators. Alerts and the approach history are in an expander below, followed by **Upcoming Approaches**: NASA/JPL's predicted Earth passes of every asteroid with a cached orbit (next 30 days, year or 10 years), scored by the same anomaly rules, so a close or hazardous pass is flagged before it happens (for example 2007 WA at 4.66 LD on 2029-05-13). **▸** tracks the asteroid.
* The 3D viewport has two views and a toggle inside it:
  * **Earth-centred:** Earth, the 1 LD ring (the Moon's distance), scale rings, and the asteroid's path 3 days either side of its latest pass. The direction comes from the orbits of the asteroid and Earth; the closest point is drawn at NASA's miss distance, and the legend shows what the orbit model alone predicted (for 2001 KF54: 18.74 LD vs NASA's 18.84 LD).
  * **Sun-centred:** the Sun, Earth's orbit and the asteroid's orbit, with both bodies placed on the pass date.

  The orbit is read from the local `orbital_elements` cache; NASA's `/neo/{id}` is called only on a miss, and the result is cached. If NASA has no usable orbit or can't be reached, the viewport shows a clearly labelled straight-line pass at NASA's miss distance and speed instead. Paths and orbits are to scale; the Earth and Sun spheres are enlarged and the legend says by how much. three.js is loaded from cdn.jsdelivr.net (pinned to one release), so the viewport needs internet access; without it, it says so and the rest of the page works.

If the database is unavailable or empty it falls back to the live NASA API (last 3 days, one quick retry, so the page never waits minutes on a rate limit), and scores alerts on the fly. Pipeline status and history come from the database only. Each part degrades on its own: if, say, the alerts table doesn't exist yet, the rest of the page still loads.

After each pipeline run, the orbits of that run's asteroids are fetched into the local cache, warning and critical alerts first, up to `--warm-limit` (default 100; fresh cached orbits cost no NASA call; `--no-warm-orbits` turns this off). Each orbit comes with NASA's list of Earth approaches (1900–2200), which feeds the Upcoming Approaches panel. To cover data loaded earlier, re-run its date range once, e.g. `scripts/run_pipeline.sh --start 2026-10-20 --end 2026-11-22 --warm-limit 250` (about 1.5 minutes for 200 asteroids).

Failures are not cached, so the page recovers as soon as a source is back, but a source that just failed is skipped for a cooldown (database 30 s, NASA 120 s) so that clicking around a degraded page stays instant. **Refresh data** clears the caches and the cooldowns. The Diagnostics panel in the sidebar says why a source was skipped.

---

### 6. REST API and the interactive dashboard

```bash
neo-api            # http://localhost:8010  (dashboard)  ·  /api/v1  (API)  ·  /api/docs  (contract)
```

The API is **contract first**: [`openapi.yaml`](openapi.yaml) (OpenAPI 3.1) was written, linted with
`npx @redocly/cli lint openapi.yaml` and committed before any code, and CI lints it on every push.
Later contract changes are recorded as ADRs in [`docs/adr/`](docs/adr/).

| Operation | What it does |
|---|---|
| `GET /api/v1/objects` | Filter (`q`, `hazardous`, `max_distance_ld`, `min_velocity_km_s`, `min_severity`, `watched`, `approach_from`/`approach_to`), sort, paginate (`limit`/`offset`, `total`, `next_offset`/`prev_offset`) |
| `GET /api/v1/objects/{id}` | Every attribute: latest and recorded passes, cached orbit (all `null` with `tracked: false` when not cached), next predicted pass, alerts, watchlist entry; `404` if unknown |
| `POST /api/v1/watchlist` | Watch an object: validated body, `201` / `400` / `409` / `422`, optional `Idempotency-Key` |
| `GET /api/v1/watchlist` | The watchlist, newest first ([ADR 0002](docs/adr/0002-watchlist-listing-and-watched-flag.md)) |
| `GET /api/v1/summary` | KPI aggregates ([ADR 0001](docs/adr/0001-summary-endpoint-for-dashboard-kpis.md)) |

Every error has the same `ErrorResponse` body (`code`, `message`, `details[{field, reason}]`, `request_id`,
`timestamp`) and `X-Request-ID` header: `400` unreadable request, `404`, `409` already watched, `422` body
breaks the rules / unknown object / idempotency key reused, `429` rate limited (`Retry-After`), `500` bug,
`503` database down. Driver errors and stack traces stay in the server log under the request ID.

**Idempotent writes.** Send the same `Idempotency-Key` to retry `POST /watchlist` safely: the same key and
body replays the original `201` (`Idempotent-Replayed: true`), the same key with a different body is a `422`.
A PostgreSQL advisory lock serialises concurrent retries of one key; keys expire after 24 hours; only
successful writes are stored, so a failed request can be retried with its key.

**Contract tests** (`tests/api/`): every response in the API tests is validated against `openapi.yaml`
(`tests/contract.py`), the routes must equal the contract's operations, and parametrised cases prove the
API accepts exactly the bodies and query values the contract's JSON Schema accepts.

**The dashboard** (`src/planetary_defense/web/static/`, plain ES modules, no build step) reads only through
the API, which a test enforces. It has KPI cards from `/summary` (click a severity, a day or a figure to filter);
live debounced search; presets and sliders, all kept in the URL; an approach map synchronised with the table
(hover highlights both, drag across dates to filter); a detail drawer that shows unknown values as
"N/A (untracked)"; a watchlist form that shows the API's field errors and retries with the same idempotency
key ("Test: send twice" demonstrates the replay); a compare tray; empty and error states with Retry; and
keyboard shortcuts (`/` `j` `k` `Enter` `w` `x` `[` `]` `1`–`6` `0` `Esc` `?`).

## Code Layout

The code is one package, `src/planetary_defense/`, in four layers. Each layer imports only from the layers inside it: `domain` imports nothing from the project (and no Streamlit, HTTP or SQL libraries), `application` imports only `domain`, and the entry points (`cli.py`, `ui/app.py`) are the only modules that pick concrete adapters.

```text
app.py                      # `streamlit run app.py` starts ui/app.py
src/planetary_defense/
├── config.py               # Settings: the only code that reads .env / the environment
├── cli.py                  # Pipeline entry point: arguments, wiring, CLI_OUTCOMES (exit codes)
├── domain/
│   ├── models.py           # Asteroid, CloseApproach, Observation, latest(), FeedBatch, runs, alerts, orbits
│   ├── errors.py           # The NeoError hierarchy (classification only)
│   ├── units.py            # AU and lunar distance (NeoWs convention: 1 LD = 1/389 AU)
│   ├── anomaly_rules.py    # Composable alert rules (distance tiers, velocity, size, hazard flag) and thresholds
│   └── orbital_math.py     # Kepler solver, positions, Earth ephemeris, orbit paths, approach trajectories
├── application/
│   ├── ports.py            # Protocols: feed, stores, readers, run log, alerts, orbit source and cache
│   ├── ingest_feed.py      # IngestService, LogObserver, IngestionRunRecorder, AlertRecorder, DryRunStore
│   ├── enrich_orbit.py     # OrbitEnricher: cache-first /neo/{id} lookups, Earth- and Sun-centred views
│   └── dashboard_service.py# Dashboard views from a store or a feed; FallbackReader
├── infrastructure/
│   ├── nasa_client.py      # NASA HTTP client (/feed, /neo/{id}): retry profiles, HTTP errors to typed errors
│   ├── nasa_dto.py         # NASA JSON shapes, validation, mapping to Observation
│   ├── db.py               # PostgreSQL connections
│   ├── repository.py       # Asteroid, IngestionRun, Orbit (cache) and Alert repositories
│   └── error_boundary.py   # CircuitBreaker for interactive callers
└── ui/
    ├── app.py              # LiveDashboard (sources, caches, breakers) and the page layout (render)
    ├── state.py            # Which asteroid the inspector shows (shared by tables and picker)
    ├── messages.py         # How the dashboard words errors, pipeline status and orbit provenance
    ├── theme.py            # ASTRAEA palette, severity colours, styling layer
    └── components/         # command_header, kpi_strip, hazard_alerts, approach_table, orbit_stage
        └── orbital_3d/     # bridge.py (geometry -> JSON -> page) and frontend/ (index.html, scene.js)
```

Adapters translate library exceptions (`requests`, `pydantic`, `psycopg`) into `NeoError` subclasses. Entry points catch only `NeoError`; anything else is a bug and crashes with a traceback instead of being reported as an outage. How an error is presented is decided by each entry point: `cli.CLI_OUTCOMES` (exit code and log label) and `ui.messages.UI_MESSAGES` (dashboard text).

### Two tiers of data

* **Tier 1, batch (`/feed`):** each pipeline window is validated, stored, and scored by the anomaly rules; `AlertRecorder` saves the alerts to `anomaly_alerts`. Re-running a date range replaces that range's alerts, so changed thresholds apply to it.
* **Tier 2, lazy enrichment (`/neo/{id}`):** `OrbitEnricher.lookup` reads `orbital_elements` first and asks NASA only on a miss or when the cached orbit is older than `ORBIT_CACHE_MAX_AGE_DAYS` (30). If NASA is down or rate-limited it serves the stale copy with a note; with nothing cached it raises `OrbitUnavailable`. A broken cache never blocks a fresh fetch. A cache hit takes milliseconds; NASA took about 2 s.

Thresholds and the cache age can be overridden in `.env` (see `.env.example`).

### Anomaly rules and orbital math

A rule is any function `Observation -> AnomalyAlert | None`. `anomaly_rules.py` builds the defaults from `AlertThresholds` and combines them with `all_of`, so a new rule is a new function, not a subclass:

| Rule | Default | Severity |
|---|---|---|
| `close_approach` | within 1 / 5 / 10 LD | critical / warning / watch |
| `hazardous_close_pass` | potentially hazardous and within 20 LD | warning |
| `high_velocity` | at least 30 km/s | watch |
| `large_diameter` | at least 1 km (largest estimate) | watch |
| `potentially_hazardous` | NASA's hazard flag | info |

`orbital_math.py` turns Keplerian elements into positions (J2000 ecliptic frame). For the Earth-centred view, `approach_trajectory` takes the asteroid's *direction* from its orbit and Earth's, and its *distance* at closest approach from NASA, because a two-body model is far less accurate than NASA at lunar-distance scale. It also reports what the model alone predicted (`model_miss_ld`), so the UI can say how approximate the drawn path is.

Which close approach is kept is decided in one place, `domain.latest()`. The parser keeps every approach it sees, and the repository stores the newest per asteroid, so keeping the full history later only needs a new table and a second write.

---

## 🧪 Running Tests

```bash
pip install -e ".[dev]"
ruff check .    # lint
pyright         # type-check; enforces that adapters and test fakes match application/ports.py
pytest
```

Run `pyright` with the venv activated (or `pyright --pythonpath venv/bin/python`), so it sees the installed libraries.

Tests mirror the package: `tests/domain/`, `tests/application/`, `tests/infrastructure/`, `tests/ui/` (including the whole page rendered with Streamlit's `AppTest` and a fake dashboard), plus `tests/test_cli.py` (arguments and exit codes end to end) and `tests/test_config.py`. Shared NeoWs payload builders live in `tests/support.py`, and in-memory fakes for every port in `tests/fakes.py`.

Database tests use the PostgreSQL from `.env` but never touch your data: they build a throwaway schema from `db/migrations/`, roll back every test, and drop the schema afterwards. If PostgreSQL isn't running, they are skipped.

---

## Database Migrations

The schema lives in versioned SQL files in `db/migrations/`. dbmate records applied files in a `schema_migrations` table and only runs new ones, so schema changes reach existing databases without wiping data.

```bash
docker compose run --rm migrate status            # what's applied / pending
docker compose run --rm migrate new add_something # creates db/migrations/<timestamp>_add_something.sql
docker compose run --rm migrate up                # apply pending migrations
```

| Migration | Creates |
|---|---|
| `20261118000000_create_asteroids` | `asteroids`: current state, one row per asteroid with its newest close approach |
| `20261122120000_create_close_approaches` | `close_approaches`: every recorded pass (history), seeded from `asteroids` |
| `20261122120100_create_ingestion_runs` | `ingestion_runs`: one row per pipeline run, shown on the dashboard |
| `20261122140000_create_orbital_elements` | `orbital_elements`: cached orbits from `/neo/{id}` (any NEO, no foreign key) |
| `20261122140100_create_anomaly_alerts` | `anomaly_alerts`: alerts per close approach and rule, scored at ingest |
| `20261122200000_create_orbit_approaches` | `orbit_approaches`: NASA's Earth approaches (past and predicted) for each cached orbit, replaced with the orbit; `orbital_elements.approaches_known` |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-how-the-data-is-stored) for how they are written.

Each file has a `-- migrate:up` section and a `-- migrate:down` section. Rules:

* Never edit a migration that has already been applied; add a new one.
* If you change a `NUMERIC` column, update the matching limit in `src/planetary_defense/infrastructure/nasa_dto.py`. A test fails if they drift apart.
* Prefer changes that don't lock or rewrite the table: add nullable columns, use `CREATE INDEX CONCURRENTLY` (with `-- migrate:up transaction:false`), and add `NOT NULL` via a `CHECK ... NOT VALID` constraint that is validated afterwards.
* Put only **one** `CONCURRENTLY` statement in a `transaction:false` file. dbmate sends the whole section at once, and Postgres runs multiple statements as a single transaction, which `CONCURRENTLY` rejects. Two indexes means two files.
* Files are applied in timestamp order, and `DBMATE_STRICT` rejects a file older than one already applied. If that happens after a merge, rename your file to a new timestamp (`migrate new` gives you one).

### CI

[.github/workflows/migrations.yml](.github/workflows/migrations.yml) runs on every push and PR. On a fresh PostgreSQL it:

1. applies the base branch's migrations, then the PR's with `--strict`, which fails if the PR adds a migration that sorts before one already on the base branch;
2. rolls every migration back and applies them again, which checks that each `-- migrate:down` really undoes its `up`;
3. lints with ruff, type-checks with pyright and runs the test suite against the migrated database.
