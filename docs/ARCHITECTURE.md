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
        R -->|"step 2: data"| P["pipeline.py"]
        subgraph Docker
            DB[("PostgreSQL<br/>asteroids table")]
        end
        M --> DB
        P -->|"save (upsert)"| DB
        A["app.py<br/>Streamlit dashboard"] -->|"read"| DB
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
| `pipeline.py` | Data step: fetch from NASA, check, save to Postgres |
| `app.py` | Dashboard that reads the saved data |

---

## 2. What one pipeline run does

```mermaid
flowchart TD
    A["Read arguments<br/>--start / --end / --dry-run"] --> B["Split dates into 7-day windows"]
    B --> C["Connect to the database first<br/>(so NASA calls aren't wasted)"]
    C --> D{"Another window?"}
    D -->|yes| E["client.py<br/>GET /feed, with retries"]
    E --> F["models.py<br/>check each asteroid,<br/>skip bad ones, remove duplicates"]
    F --> G["database.py<br/>save in 1 transaction<br/>(only new or changed rows)"]
    G --> D
    D -->|no| H["Print summary, exit 0"]
    E -.->|"NASA failed"| X["Stop: exit 3<br/>(or 5 if earlier windows were saved)"]
    G -.->|"database failed"| Y["Stop: exit 4<br/>(or 5 if earlier windows were saved)"]
```

Each window is saved in its own transaction, so a run that stops halfway keeps the finished windows. Re-running the same dates is always safe.

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Configuration error (for example, missing `.env` settings) |
| 2 | Invalid arguments |
| 3 | NASA API unavailable or returned unusable data |
| 4 | Database error |
| 5 | Partial run: re-run the same dates to finish |
| 6 | Schema step failed, or migrations are pending (`run_pipeline.sh` / `migrate.sh` only) |
| 130 | Interrupted (Ctrl+C) |

---

## 3. Which file depends on which

```mermaid
flowchart TD
    pipeline["pipeline.py<br/>command line + order of steps"] --> client["client.py<br/>talks to NASA"]
    pipeline --> models["models.py<br/>checks the data"]
    pipeline --> database["database.py<br/>talks to Postgres"]
    database --> models
    app["app.py<br/>dashboard"] --> client
    app --> models
    app --> database
    models -.->|"copies column size limits from"| mig["db/migrations/*.sql"]
    database -.->|"expects the table defined in"| mig
```

Solid arrows are Python imports. Dotted arrows are assumptions kept in sync by hand. A test fails if the column size limits in `models.py` drift from the migrations.

---

## 4. How migrations reach the database

```mermaid
flowchart LR
    N["migrate new name<br/>creates timestamped .sql"] --> PR["Open a PR"]
    PR --> CI{"CI on fresh Postgres"}
    CI -->|"1"| S["Apply main's migrations,<br/>then this branch's with --strict"]
    CI -->|"2"| RB["Roll everything back,<br/>apply again"]
    CI -->|"3"| T["Run pytest"]
    S & RB & T --> MG["Merge"]
    MG --> RUN["Next daily run or<br/>run_pipeline.sh"]
    RUN --> UP["dbmate up<br/>applies pending files in timestamp order"]
    UP --> DB[("PostgreSQL<br/>schema_migrations records each file")]
```

dbmate applies only files not yet recorded in `schema_migrations`, oldest timestamp first. Strict mode rejects a file older than one already applied.

---

## 5. How the data is stored

### Current

One row per asteroid. Only the **latest** close approach is kept; earlier approaches are overwritten.

```mermaid
erDiagram
    ASTEROIDS {
        text neo_reference_id PK
        text name
        text nasa_jpl_url
        numeric absolute_magnitude_h
        numeric estimated_diameter_min_km
        numeric estimated_diameter_max_km
        boolean is_potentially_hazardous
        boolean is_sentry_object
        date close_approach_date "latest only"
        numeric relative_velocity_km_s "latest only"
        numeric miss_distance_km "latest only"
        text orbiting_body "latest only"
        timestamptz created_at
        timestamptz updated_at
    }
```

### Proposed

Facts about the asteroid live in one table and every close approach is kept in another, so history is preserved. `INGESTION_RUNS` records the result of each pipeline run.

```mermaid
erDiagram
    ASTEROIDS ||--o{ CLOSE_APPROACHES : has
    ASTEROIDS {
        text neo_reference_id PK
        text name
        numeric absolute_magnitude_h
        numeric estimated_diameter_min_km
        numeric estimated_diameter_max_km
        boolean is_potentially_hazardous
        boolean is_sentry_object
        timestamptz updated_at
    }
    CLOSE_APPROACHES {
        text neo_reference_id FK
        date close_approach_date
        text orbiting_body
        numeric relative_velocity_km_s
        numeric miss_distance_km
    }
    INGESTION_RUNS {
        bigint id PK
        date start_date
        date end_date
        int exit_code
        int inserted
        int updated
        timestamptz finished_at
    }
```

`CLOSE_APPROACHES` would be unique on `(neo_reference_id, close_approach_date, orbiting_body)`, so re-runs stay safe.
