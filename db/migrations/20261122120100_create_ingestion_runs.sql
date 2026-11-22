-- One row per pipeline run that got as far as fetching (successful, partial or
-- failed), written by ingest.IngestionRunRecorder. The dashboard reads it to
-- show when data was last refreshed and whether the last run failed.
--
-- Runs that stop before fetching (missing settings, database down) cannot be
-- recorded; the dashboard's staleness warning covers them.

-- migrate:up
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          TEXT NOT NULL,
    windows_total   INTEGER NOT NULL,
    windows_done    INTEGER NOT NULL,
    fetched         INTEGER NOT NULL,
    inserted        INTEGER NOT NULL,
    updated         INTEGER NOT NULL,
    unchanged       INTEGER NOT NULL,
    invalid         INTEGER NOT NULL,
    missing_dates   INTEGER NOT NULL,
    new_approaches  INTEGER NOT NULL,
    -- Class name of the errors.NeoError that stopped the run, e.g. UpstreamAuthError.
    error_category  TEXT,
    error_message   TEXT,

    CONSTRAINT chk_ingestion_runs_status CHECK (status IN ('success', 'partial', 'failed')),
    CONSTRAINT chk_ingestion_runs_dates CHECK (start_date <= end_date),
    CONSTRAINT chk_ingestion_runs_error CHECK ((status = 'success') = (error_category IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_finished_at
    ON ingestion_runs (finished_at DESC);

-- migrate:down
DROP TABLE IF EXISTS ingestion_runs;
