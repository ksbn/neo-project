-- Initial schema for NASA NeoWs (Near Earth Object Web Service) data.
--
-- This is the original schema.sql, unchanged. Its IF NOT EXISTS clauses make it
-- safe to apply to a database that was created by the old
-- /docker-entrypoint-initdb.d mount: dbmate just records it as applied.
--
-- Never edit this file once applied; add a new migration instead:
--   docker compose run --rm migrate new <description>

-- migrate:up
CREATE TABLE IF NOT EXISTS asteroids (
    -- NASA reference ID; primary key enables idempotent upserts:
    --   INSERT ... ON CONFLICT (neo_reference_id) DO UPDATE SET ...
    neo_reference_id                  TEXT PRIMARY KEY,
    name                              TEXT NOT NULL,
    nasa_jpl_url                      TEXT,
    absolute_magnitude_h              NUMERIC(6, 3),
    estimated_diameter_min_km         NUMERIC(14, 6),
    estimated_diameter_max_km         NUMERIC(14, 6),
    is_potentially_hazardous          BOOLEAN NOT NULL DEFAULT FALSE,
    is_sentry_object                  BOOLEAN NOT NULL DEFAULT FALSE,

    -- Close approach details (from close_approach_data in the feed)
    close_approach_date               DATE,
    relative_velocity_km_s            NUMERIC(14, 6),
    miss_distance_km                  NUMERIC(20, 3),
    orbiting_body                     TEXT,

    -- Bookkeeping
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_diameter_range
        CHECK (estimated_diameter_min_km IS NULL
               OR estimated_diameter_max_km IS NULL
               OR estimated_diameter_min_km <= estimated_diameter_max_km)
);

CREATE INDEX IF NOT EXISTS idx_asteroids_close_approach_date
    ON asteroids (close_approach_date);

CREATE INDEX IF NOT EXISTS idx_asteroids_hazardous
    ON asteroids (is_potentially_hazardous);

-- migrate:down
DROP TABLE IF EXISTS asteroids;
