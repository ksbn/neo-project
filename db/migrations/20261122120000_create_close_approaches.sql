-- Every close approach the pipeline has seen, so history is no longer
-- overwritten. The asteroids table keeps its "newest approach" columns as the
-- current-state view the dashboard lists.
--
-- Seeded from asteroids, so the approaches already stored are not lost.

-- migrate:up
CREATE TABLE IF NOT EXISTS close_approaches (
    neo_reference_id        TEXT NOT NULL
        REFERENCES asteroids (neo_reference_id) ON DELETE CASCADE,
    close_approach_date     DATE NOT NULL,
    orbiting_body           TEXT NOT NULL,
    -- Same types as the matching asteroids columns (models.py limits apply to both).
    relative_velocity_km_s  NUMERIC(14, 6) NOT NULL,
    miss_distance_km        NUMERIC(20, 3) NOT NULL,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per pass: re-runs upsert instead of duplicating.
    PRIMARY KEY (neo_reference_id, close_approach_date, orbiting_body)
);

CREATE INDEX IF NOT EXISTS idx_close_approaches_date
    ON close_approaches (close_approach_date);

INSERT INTO close_approaches
    (neo_reference_id, close_approach_date, orbiting_body, relative_velocity_km_s, miss_distance_km)
SELECT neo_reference_id, close_approach_date, orbiting_body, relative_velocity_km_s, miss_distance_km
FROM asteroids
WHERE close_approach_date IS NOT NULL
  AND orbiting_body IS NOT NULL
  AND relative_velocity_km_s IS NOT NULL
  AND miss_distance_km IS NOT NULL
ON CONFLICT DO NOTHING;

-- migrate:down
DROP TABLE IF EXISTS close_approaches;
