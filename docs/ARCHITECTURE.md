# Architecture

High-level diagrams of how the NASA NeoWs pipeline fits together. They render on GitHub, and in the VS Code Markdown preview with a Mermaid extension.

- [1. The big picture](#1-the-big-picture)
- [2. What one pipeline run does](#2-what-one-pipeline-run-does)
- [3. Which file depends on which](#3-which-file-depends-on-which)
- [4. How migrations reach the database](#4-how-migrations-reach-the-database)
- [5. How the data is stored](#5-how-the-data-is-stored)

---

## 1. The big picture

Who runs what, and where the data goes.

```mermaid
flowchart LR
    subgraph Mac["Your Mac"]
        L["launchd<br/>daily timer"] --> D["daily_pipeline.sh<br/>wait for network + Docker"]
        You(["You"]) --> R
        D --> R["run_pipeline.sh<br/>combines the 2 steps"]
        R -->|"step 1: schema"| M["migrate.sh<br/>dbmate"]
        R -->|"step 2: data"| P["planetary_defense.cli"]
        subgraph Docker
            DB[("PostgreSQL<br/>asteroids table")]
        end
        M --> DB
        P -->|"save (upsert)"| DB
        A["ui/app.py<br/>Streamlit dashboard"] -->|"read"| DB
    end
    NASA[("NASA NeoWs API")]
    P -->|"fetch, 7 days at a time"| NASA
    A -.->|"backup source if DB is empty or down"| NASA
    GH["GitHub Actions CI"] -.->|"checks every PR"| Repo["Git repo"]
```

| Piece | Job |
|---|---|
| `scripts/daily_pipeline.sh` | Started by launchd. Waits for the network and Docker, then calls `run_pipeline.sh` |
| `scripts/run_pipeline.sh` | The only place that combines the schema step and the data step |
| `scripts/migrate.sh` | Schema step: applies or checks migrations with dbmate |
| `planetary_defense.cli` | Data step: fetch from NASA, check, save to Postgres |
| `planetary_defense.ui.app` | Dashboard that reads the saved data (`streamlit run app.py`) |

---

## 2. What one pipeline run does

```mermaid
flowchart TD
    A["Read arguments<br/>--start / --end / --dry-run"] --> B["Split dates into 7-day windows"]
    B --> C["Connect to the database first<br/>(so NASA calls aren't wasted)"]
    C --> D{"Another window?"}
    D -->|yes| E["nasa_client.py<br/>GET /feed, with retries"]
    E --> F["nasa_dto.py<br/>check each asteroid,<br/>skip bad ones"]
    F --> G["repository.py<br/>keep the newest approach per asteroid,<br/>save in 1 transaction<br/>(only new or changed rows)"]
    G --> D
    D -->|no| H["Print summary, exit 0"]
    E -.->|"NASA failed"| X["Stop: exit 3, or 1 if the key was rejected,<br/>or 7 if the request or format was rejected<br/>(5 if earlier windows were saved)"]
    G -.->|"database failed"| Y["Stop: exit 4<br/>(or 5 if earlier windows were saved)"]
```

Each window is saved in its own transaction, so a run that stops halfway keeps the finished windows. Re-running the same dates is always safe.

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Configuration error (missing `.env` settings, or NASA rejected the API key) |
| 2 | Invalid arguments |
| 3 | NASA API unavailable or rate limited |
| 4 | Database error |
| 5 | Partial run: re-run the same dates to finish |
| 6 | Schema step failed, or migrations are pending (`run_pipeline.sh` / `migrate.sh` only) |
| 7 | NASA rejected the request, or its response format changed |
| 130 | Interrupted (Ctrl+C) |

---

## 3. Which file depends on which

```mermaid
flowchart TD
    subgraph Entry["Entry points"]
        pipeline["cli.py<br/>CLI, wiring, CLI_OUTCOMES"]
        app["ui/app.py<br/>wiring, breakers, rendering"]
        ui["ui/messages.py<br/>UI_MESSAGES"]
    end
    subgraph Services["Application services"]
        ingest["application/ingest_feed.py<br/>IngestService, observers"]
        dash["application/dashboard_service.py<br/>views, FallbackReader"]
        res["infrastructure/error_boundary.py<br/>CircuitBreaker"]
    end
    subgraph Core["Domain"]
        ports["application/ports.py<br/>FeedSource, AsteroidStore, AsteroidReader, RunLog"]
        domain["domain/models.py<br/>Asteroid, CloseApproach, Observation, latest"]
        errors["domain/errors.py<br/>NeoError tree"]
    end
    subgraph Adapters["Adapters"]
        client["infrastructure/nasa_client.py<br/>talks to NASA"]
        models["infrastructure/nasa_dto.py<br/>NASA JSON shapes"]
        database["infrastructure/repository.py + db.py<br/>talks to Postgres"]
        config["config.py<br/>reads .env"]
    end
    pipeline --> ingest
    pipeline --> config
    pipeline -->|"builds"| client
    pipeline -->|"builds"| database
    app --> dash
    app --> res
    app --> ui
    app --> config
    app -->|"builds"| client
    app -->|"builds"| database
    ingest --> ports
    dash --> ports
    ports --> domain
    ingest --> domain
    client -. "satisfies" .-> ports
    database -. "satisfies" .-> ports
    client --> models
    models --> domain
    database --> domain
    client --> errors
    models --> errors
    database --> errors
    config --> errors
    models -.->|"copies column size limits from"| mig["db/migrations/*.sql"]
    database -.->|"expects the table defined in"| mig
```

Solid arrows are Python imports. Services and ports import only the domain; the entry points are the only modules that pick concrete adapters. Dotted arrows to `application/ports.py` mean "has the methods the protocol asks for"; nothing inherits, and pyright checks it in CI. Dotted arrows to migrations are assumptions kept in sync by hand. Tests fail if the column size limits in `nasa_dto.py` or the column list in `repository.py` drift from the migrations.

Errors: each adapter turns its library's exceptions into one `NeoError` subclass (`UpstreamUnavailable`, `UpstreamRateLimited`, `UpstreamAuthError`, `UpstreamRequestError`, `FeedContractError`, `StorageError`, `ConfigError`). The classes only classify. `cli.CLI_OUTCOMES` maps them to exit codes and `ui.messages.UI_MESSAGES` to dashboard text, both by the most specific class, so a new subclass can never crash a handler.

---

## 4. How migrations reach the database

```mermaid
flowchart LR
    N["migrate new name<br/>creates timestamped .sql"] --> PR["Open a PR"]
    PR --> CI{"CI on fresh Postgres"}
    CI -->|"1"| S["Apply main's migrations,<br/>then this branch's with --strict"]
    CI -->|"2"| RB["Roll everything back,<br/>apply again"]
    CI -->|"3"| T["pyright, then pytest"]
    S & RB & T --> MG["Merge"]
    MG --> RUN["Next daily run or<br/>run_pipeline.sh"]
    RUN --> UP["dbmate up<br/>applies pending files in timestamp order"]
    UP --> DB[("PostgreSQL<br/>schema_migrations records each file")]
```

dbmate applies only files not yet recorded in `schema_migrations`, oldest timestamp first. Strict mode rejects a file older than one already applied.

---

## 5. How the data is stored

Three tables. `asteroids` is the **current state**: one row per asteroid with its newest close approach, which is what the dashboard lists. `close_approaches` is the **history**: every pass the pipeline has seen. `ingestion_runs` records **what each pipeline run did**, which the dashboard shows as "last run".

```mermaid
erDiagram
    ASTEROIDS ||--o{ CLOSE_APPROACHES : "has passes"
    ASTEROIDS {
        text neo_reference_id PK
        text name
        text nasa_jpl_url
        numeric absolute_magnitude_h
        numeric estimated_diameter_min_km
        numeric estimated_diameter_max_km
        boolean is_potentially_hazardous
        boolean is_sentry_object
        date close_approach_date "newest pass"
        numeric relative_velocity_km_s "newest pass"
        numeric miss_distance_km "newest pass"
        text orbiting_body "newest pass"
        timestamptz created_at
        timestamptz updated_at
    }
    CLOSE_APPROACHES {
        text neo_reference_id PK, FK
        date close_approach_date PK
        text orbiting_body PK
        numeric relative_velocity_km_s
        numeric miss_distance_km
        timestamptz created_at
        timestamptz updated_at
    }
    INGESTION_RUNS {
        bigint id PK
        timestamptz started_at
        timestamptz finished_at
        date start_date
        date end_date
        text status "success, partial, failed"
        int inserted
        int updated
        int new_approaches
        text error_category "NeoError class name"
        text error_message
    }
```

How one window is written (all in one transaction, `AsteroidRepository.save`):

1. `domain.models.latest()` picks the newest observation per asteroid, which is upserted into `asteroids`. The SQL `WHERE` guard also refuses to replace a newer approach, so an old backfill or an overlapping run can't move it backwards.
2. Every observation's approach is upserted into `close_approaches`, keyed by `(neo_reference_id, close_approach_date, orbiting_body)`. A new pass adds a row; a pass NASA revised updates its numbers; a re-run changes nothing.

The `close_approaches` migration seeded the table from the approaches already in `asteroids`, so history starts with what was stored before it existed.

`ingestion_runs` gets one row per run that started fetching, written by `application.ingest_feed.IngestionRunRecorder` over its own autocommit connection, so a failed or partial run is recorded even after its data transaction rolled back. Runs that stop earlier (missing settings, database down) can't be recorded; the dashboard flags data older than 36 hours instead. Dry runs record nothing.
