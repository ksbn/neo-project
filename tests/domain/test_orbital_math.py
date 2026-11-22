"""Orbital math (domain/orbital_math.py): Kepler's equation, positions, and the approach trajectory. No I/O."""

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from planetary_defense.domain.models import OrbitalElements, Vec3
from planetary_defense.domain.orbital_math import (
    EARTH,
    J2000,
    approach_trajectory,
    date_from_jd,
    earth_position,
    geocentric_position,
    heliocentric_position,
    julian_day,
    mean_motion,
    orbit_path,
    orbital_period_days,
    solve_kepler,
)
from planetary_defense.domain.units import AU_KM, LUNAR_DISTANCE_KM, au_to_ld, km_to_ld


def circle(a: float = 2.0, **kw: float) -> OrbitalElements:
    """A simple orbit at J2000; keyword arguments override any element."""
    base = OrbitalElements(
        semi_major_axis_au=a, eccentricity=0.0, inclination_deg=0.0,
        ascending_node_deg=0.0, perihelion_argument_deg=0.0, mean_anomaly_deg=0.0, epoch_jd=J2000,
    )
    return replace(base, **kw)


def close(v: Vec3, expected: tuple[float, float, float], tol: float = 1e-9) -> bool:
    return all(math.isclose(a, b, abs_tol=tol) for a, b in zip(v, expected, strict=True))


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


def test_lunar_distance_follows_the_neows_convention():
    """NeoWs reports lunar = astronomical * 389, i.e. 1 LD = 1/389 AU (measured on live data)."""
    assert au_to_ld(1.0) == pytest.approx(389.0)
    assert LUNAR_DISTANCE_KM == pytest.approx(AU_KM / 389)
    assert LUNAR_DISTANCE_KM == pytest.approx(384_570.4, abs=0.5)
    assert km_to_ld(LUNAR_DISTANCE_KM * 2.5) == pytest.approx(2.5)


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def test_julian_day_of_known_instants():
    assert julian_day(date(2000, 1, 1)) == 2451544.5  # midnight
    assert julian_day(datetime(2000, 1, 1, 12, tzinfo=UTC)) == J2000 == 2451545.0
    assert julian_day(date(2026, 11, 22)) == 2461366.5


def test_julian_day_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone"):
        julian_day(datetime(2000, 1, 1, 12))


# --------------------------------------------------------------------------- #
# Kepler's equation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("e", [0.0, 0.1, 0.5, 0.9, 0.99])
@pytest.mark.parametrize("m", [0.0, 0.3, 1.0, math.pi / 2, 3.0, math.pi, 4.5, 6.2])
def test_kepler_solution_satisfies_the_equation(e, m):
    big_e = solve_kepler(m, e)
    residual = big_e - e * math.sin(big_e) - m
    assert math.isclose(math.remainder(residual, 2 * math.pi), 0.0, abs_tol=1e-10)


def test_circular_orbit_has_eccentric_equal_to_mean_anomaly():
    assert solve_kepler(1.234, 0.0) == pytest.approx(1.234)


@pytest.mark.parametrize("e", [-0.1, 1.0, 1.5])
def test_only_elliptical_orbits_are_supported(e):
    with pytest.raises(ValueError, match="eccentricity"):
        solve_kepler(1.0, e)


# --------------------------------------------------------------------------- #
# Heliocentric positions
# --------------------------------------------------------------------------- #


def test_circular_orbit_moves_a_quarter_turn_in_a_quarter_period():
    orbit = circle(a=2.0)
    period = 2 * math.pi / mean_motion(orbit.semi_major_axis_au)
    assert close(heliocentric_position(orbit, J2000), (2.0, 0.0, 0.0))
    assert close(heliocentric_position(orbit, J2000 + period / 4), (0.0, 2.0, 0.0), tol=1e-9)
    assert close(heliocentric_position(orbit, J2000 + period), (2.0, 0.0, 0.0), tol=1e-9)


def test_orbital_period_follows_keplers_third_law():
    one_au_period_days = 2 * math.pi / mean_motion(1.0)
    assert one_au_period_days == pytest.approx(365.2569, abs=1e-3)  # sidereal year
    assert 2 * math.pi / mean_motion(4.0) == pytest.approx(one_au_period_days * 8)


def test_eccentric_orbit_reaches_perihelion_and_aphelion_distances():
    orbit = circle(a=1.0, eccentricity=0.5)
    half_period = math.pi / mean_motion(1.0)
    assert math.dist(heliocentric_position(orbit, J2000), (0, 0, 0)) == pytest.approx(0.5)
    assert math.dist(heliocentric_position(orbit, J2000 + half_period), (0, 0, 0)) == pytest.approx(1.5)


def test_inclination_lifts_the_orbit_out_of_the_ecliptic():
    orbit = circle(a=1.0, inclination_deg=30.0, mean_anomaly_deg=90.0)  # argument of latitude 90 degrees
    assert close(heliocentric_position(orbit, J2000), (0.0, math.cos(math.radians(30)), 0.5))


def test_ascending_node_rotates_the_orbit_about_the_ecliptic_pole():
    assert close(heliocentric_position(circle(a=1.0, ascending_node_deg=90.0), J2000), (0.0, 1.0, 0.0))


def test_earth_position_at_j2000_matches_the_ephemeris():
    """Earth on 2000-01-01 12:00 TT is near ecliptic longitude 100.4 degrees, 0.983 AU from the Sun."""
    x, y, z = earth_position(J2000)
    assert (x, y) == pytest.approx((-0.1771, 0.9672), abs=0.005)
    assert abs(z) < 1e-4


def test_earth_is_closest_in_early_january_and_farthest_in_early_july():
    def sun_distance(day: date) -> float:
        return math.dist(earth_position(julian_day(day)), (0, 0, 0))

    assert sun_distance(date(2026, 1, 3)) == pytest.approx(0.9833, abs=0.0005)
    assert sun_distance(date(2026, 7, 6)) == pytest.approx(1.0167, abs=0.0005)


# --------------------------------------------------------------------------- #
# Orbit path (Sun-centred view)
# --------------------------------------------------------------------------- #


def test_orbit_path_is_a_closed_ellipse_between_perihelion_and_aphelion():
    orbit = circle(a=1.5, eccentricity=0.3, inclination_deg=12.0, ascending_node_deg=40.0, perihelion_argument_deg=70.0)
    path = orbit_path(orbit, samples=180)

    assert len(path) == 181
    assert close(path[0], path[-1])
    radii = [math.dist(p, (0, 0, 0)) for p in path]
    assert min(radii) == pytest.approx(1.5 * 0.7, rel=1e-3)
    assert max(radii) == pytest.approx(1.5 * 1.3, rel=1e-3)


def test_orbit_path_needs_at_least_three_samples():
    with pytest.raises(ValueError, match="samples"):
        orbit_path(circle(), samples=2)


# --------------------------------------------------------------------------- #
# Earth-centred view
# --------------------------------------------------------------------------- #


def test_an_orbit_identical_to_earths_stays_at_the_origin():
    assert close(geocentric_position(EARTH, J2000), (0.0, 0.0, 0.0), tol=1e-12)
    # Earth's own elements drift slowly (Standish rates); a fixed J2000 orbit stays within a few 1e-5 AU for months.
    assert math.dist(geocentric_position(EARTH, J2000 + 123.4), (0, 0, 0)) < 1e-4


def test_earth_position_uses_earths_own_drifting_elements():
    assert earth_position(J2000) == heliocentric_position(EARTH, J2000)


# An Earth-like orbit, tilted by 1 degree and slightly more eccentric. It crosses Earth's orbital
# plane near the March equinox, where it passes Earth at under a lunar distance or so.
NEIGHBOUR = replace(EARTH, inclination_deg=1.0, eccentricity=EARTH.eccentricity + 0.001)
APPROACH_DAY = datetime(2026, 3, 20, tzinfo=UTC)


def test_trajectory_places_the_closest_point_at_nasas_miss_distance():
    trajectory = approach_trajectory(NEIGHBOUR, APPROACH_DAY, miss_distance_ld=2.0, window_days=3, steps_per_day=24)

    # The exact closest point (between samples) is at NASA's distance; the nearest sample is within 0.1%.
    assert math.dist(trajectory.closest_point_ld, (0, 0, 0)) == pytest.approx(2.0, abs=1e-9)
    closest = min(trajectory.points_ld, key=lambda p: math.dist(p, (0, 0, 0)))
    assert math.dist(closest, (0, 0, 0)) == pytest.approx(2.0, rel=1e-3)
    assert trajectory.points_ld[trajectory.closest_index] == closest
    assert 0 < trajectory.closest_index < len(trajectory.points_ld) - 1  # inside the window, not at its edge
    assert trajectory.model_miss_ld > 0 and trajectory.model_miss_ld != pytest.approx(2.0)
    assert trajectory.miss_distance_ld == 2.0


def test_trajectory_keeps_the_modelled_direction_and_relative_motion():
    trajectory = approach_trajectory(NEIGHBOUR, APPROACH_DAY, miss_distance_ld=2.0, window_days=2, steps_per_day=12)
    exact_model = au_vec_to_ld(geocentric_position(NEIGHBOUR, trajectory.closest_time_jd))
    shown_exact = trajectory.closest_point_ld
    norm = math.dist(shown_exact, (0, 0, 0)) * math.dist(exact_model, (0, 0, 0))
    cos = sum(a * b for a, b in zip(shown_exact, exact_model, strict=True)) / norm
    assert cos == pytest.approx(1.0, abs=1e-9)  # same direction as the model, only the distance is NASA's

    i = trajectory.closest_index
    model = au_vec_to_ld(geocentric_position(NEIGHBOUR, trajectory.times_jd[i]))
    shown = trajectory.points_ld[i]

    for k in (i - 5, i + 5):  # the path is the model path shifted, so motion between samples is unchanged
        moved = [a - b for a, b in zip(trajectory.points_ld[k], shown, strict=True)]
        modelled = [a - b for a, b in zip(au_vec_to_ld(geocentric_position(NEIGHBOUR, trajectory.times_jd[k])), model,
                                          strict=True)]
        assert moved == pytest.approx(modelled, abs=1e-9)


def test_trajectory_samples_are_evenly_spaced_around_the_approach():
    trajectory = approach_trajectory(NEIGHBOUR, APPROACH_DAY, miss_distance_ld=1.0, window_days=1, steps_per_day=4)
    assert len(trajectory.times_jd) == 2 * 4 + 1
    assert trajectory.times_jd[0] == pytest.approx(julian_day(APPROACH_DAY - timedelta(days=1)))
    steps = {round(b - a, 9) for a, b in zip(trajectory.times_jd, trajectory.times_jd[1:], strict=False)}
    assert steps == {0.25}


@pytest.mark.parametrize("bad", [{"miss_distance_ld": 0.0}, {"window_days": 0}, {"steps_per_day": 0}])
def test_trajectory_rejects_meaningless_arguments(bad):
    args = {"miss_distance_ld": 1.0, "window_days": 1, "steps_per_day": 4} | bad
    with pytest.raises(ValueError):
        approach_trajectory(NEIGHBOUR, APPROACH_DAY, **args)


def au_vec_to_ld(v: Vec3) -> tuple[float, float, float]:
    return (au_to_ld(v[0]), au_to_ld(v[1]), au_to_ld(v[2]))


def test_date_from_jd_inverts_julian_day():
    assert date_from_jd(2461200.5) == date(2026, 6, 9)  # Zoozve's osculation epoch
    assert date_from_jd(J2000) == date(2000, 1, 1)
    for day in (date(1999, 12, 31), date(2026, 11, 22), date(2100, 3, 1)):
        assert date_from_jd(julian_day(day)) == day


def test_orbital_period_matches_nasa_for_zoozve():
    assert orbital_period_days(0.72356318914361) == pytest.approx(224.8087, abs=1e-3)  # NASA: 224.8087046820664


def test_straight_line_flyby_passes_at_the_miss_distance_at_the_reported_speed():
    from planetary_defense.domain.orbital_math import straight_line_flyby
    from planetary_defense.domain.units import LUNAR_DISTANCE_KM

    t = straight_line_flyby(date(2026, 11, 2), 15.65, 9.5, window_days=1, steps_per_day=4)
    assert len(t.points_ld) == 9 and t.closest_index == 4
    assert math.dist(t.closest_point_ld, (0, 0, 0)) == pytest.approx(15.65)
    assert t.closest_time_jd == pytest.approx(julian_day(date(2026, 11, 2)))
    per_step = math.dist(t.points_ld[5], t.points_ld[4])
    assert per_step == pytest.approx(9.5 * 86400 / 4 / LUNAR_DISTANCE_KM)  # a quarter day at 9.5 km/s
    assert t.model_miss_ld == pytest.approx(15.65)  # no orbit model: nothing to compare
