-- The operator watchlist (POST /api/v1/watchlist) and the idempotency keys
-- that make retrying that write safe. Written by
-- infrastructure.watchlist_repository.WatchlistRepository.
--
-- watchlist: one entry per object (the business key), so watching an object
-- twice is a conflict rather than a duplicate.
--
-- idempotency_keys: the first successful response for each (scope, key), with a
-- hash of the request body, so a retry with the same key replays it and a reuse
-- with a different body is rejected. Rows expire after 24 hours.

-- migrate:up
CREATE TABLE IF NOT EXISTS watchlist (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    neo_reference_id    TEXT NOT NULL UNIQUE REFERENCES asteroids (neo_reference_id) ON DELETE CASCADE,
    priority            TEXT NOT NULL,
    alert_threshold_ld  DOUBLE PRECISION,
    note                TEXT,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),  -- real time, even within one transaction

    CONSTRAINT chk_watchlist_priority CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT chk_watchlist_threshold CHECK (alert_threshold_ld IS NULL
                                              OR (alert_threshold_ld > 0 AND alert_threshold_ld <= 100)),
    CONSTRAINT chk_watchlist_note CHECK (note IS NULL OR char_length(note) <= 500),
    CONSTRAINT chk_watchlist_tags CHECK (cardinality(tags) <= 5)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope           TEXT NOT NULL,          -- the operation, e.g. 'POST /watchlist'
    key             TEXT NOT NULL,
    request_hash    TEXT NOT NULL,          -- SHA-256 of the canonical request body
    status_code     SMALLINT NOT NULL,
    response_body   JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (scope, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires_at ON idempotency_keys (expires_at);

-- migrate:down
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS watchlist;
