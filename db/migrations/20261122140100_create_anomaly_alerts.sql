-- Alerts raised by the anomaly rules (domain/anomaly_rules.py) for each close
-- approach, written at ingest time by application.ingest_feed.AlertRecorder.
-- Re-ingesting a pass replaces its alerts, so a rule that no longer fires (or a
-- changed threshold) is reflected on the next run over those dates.

-- migrate:up
CREATE TABLE IF NOT EXISTS anomaly_alerts (
    neo_reference_id     TEXT NOT NULL
        REFERENCES asteroids (neo_reference_id) ON DELETE CASCADE,
    close_approach_date  DATE NOT NULL,
    rule                 TEXT NOT NULL,
    -- domain.models.Severity: 1 info, 2 watch, 3 warning, 4 critical
    severity             SMALLINT NOT NULL,
    message              TEXT NOT NULL,
    value                DOUBLE PRECISION,
    threshold            DOUBLE PRECISION,
    raised_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (neo_reference_id, close_approach_date, rule),
    CONSTRAINT chk_anomaly_alerts_severity CHECK (severity BETWEEN 1 AND 4)
);

CREATE INDEX IF NOT EXISTS idx_anomaly_alerts_severity_date
    ON anomaly_alerts (severity DESC, close_approach_date DESC);

-- migrate:down
DROP TABLE IF EXISTS anomaly_alerts;
