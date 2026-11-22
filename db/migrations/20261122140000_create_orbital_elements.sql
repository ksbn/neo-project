-- Local cache of orbits from NASA's /neo/{id}, so the 3D views never wait on
-- NASA (or its rate limit) for an asteroid they have shown before. Written by
-- application.enrich_orbit.OrbitEnricher through infrastructure.repository.OrbitRepository.
--
-- No foreign key to asteroids: any NEO can be cached, including ones seen
-- only through the dashboard's live NASA fallback.

-- migrate:up
CREATE TABLE IF NOT EXISTS orbital_elements (
    neo_reference_id         TEXT PRIMARY KEY,
    semi_major_axis_au       DOUBLE PRECISION NOT NULL,
    eccentricity             DOUBLE PRECISION NOT NULL,
    inclination_deg          DOUBLE PRECISION NOT NULL,
    ascending_node_deg       DOUBLE PRECISION NOT NULL,
    perihelion_argument_deg  DOUBLE PRECISION NOT NULL,
    mean_anomaly_deg         DOUBLE PRECISION NOT NULL,
    epoch_jd                 DOUBLE PRECISION NOT NULL,
    orbit_id                 TEXT,
    orbit_class              TEXT,
    moid_au                  DOUBLE PRECISION,
    fetched_at               TIMESTAMPTZ NOT NULL,

    CONSTRAINT chk_orbital_elements_ellipse
        CHECK (semi_major_axis_au > 0 AND eccentricity >= 0 AND eccentricity < 1)
);

-- migrate:down
DROP TABLE IF EXISTS orbital_elements;
