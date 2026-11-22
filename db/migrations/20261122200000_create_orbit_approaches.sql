-- Every Earth close approach NASA/JPL lists for a cached orbit (/neo/{id}
-- close_approach_data, 1900-2200): past passes and predicted future ones.
-- Written together with the orbit by infrastructure.repository.OrbitRepository,
-- which replaces an asteroid's whole list whenever its orbit is re-fetched.
--
-- Unlike close_approaches (passes the feed reported, kept as history), these
-- are NASA's predictions and are refreshed with the orbit.
--
-- orbital_elements.approaches_known separates "no approaches listed" from
-- "cached before this table existed": the latter are re-fetched on next use.

-- migrate:up
ALTER TABLE orbital_elements ADD COLUMN approaches_known BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS orbit_approaches (
    neo_reference_id        TEXT NOT NULL REFERENCES orbital_elements (neo_reference_id) ON DELETE CASCADE,
    close_approach_date     DATE NOT NULL,
    relative_velocity_km_s  DOUBLE PRECISION NOT NULL CHECK (relative_velocity_km_s >= 0),
    miss_distance_km        DOUBLE PRECISION NOT NULL CHECK (miss_distance_km >= 0),

    PRIMARY KEY (neo_reference_id, close_approach_date)
);

-- "What passes next?" across all asteroids.
CREATE INDEX IF NOT EXISTS idx_orbit_approaches_date ON orbit_approaches (close_approach_date);

-- migrate:down
DROP TABLE IF EXISTS orbit_approaches;
ALTER TABLE orbital_elements DROP COLUMN IF EXISTS approaches_known;
