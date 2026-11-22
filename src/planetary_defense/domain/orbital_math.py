"""Two-body orbital mechanics for the 3D views. Pure ``math``, no I/O.

* Sun-centred view: :func:`orbit_path` draws a full Keplerian ellipse and
  :func:`heliocentric_position` places a body on it at a given time.
* Earth-centred view: :func:`approach_trajectory` draws an asteroid's path
  around a close approach in lunar distances. Its *direction* comes from the
  orbits of the asteroid and Earth; its *distance* at closest approach is NASA's
  reported miss distance, because a two-body model propagated from an epoch
  months or years away is far less accurate than NASA's at lunar-distance scale.

All positions are heliocentric (or geocentric) vectors in the J2000 ecliptic
frame, in AU unless the name says LD. Times are Julian days (TT and UTC are not
distinguished: the 69 s difference is irrelevant here).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Final

from planetary_defense.domain.models import OrbitalElements, Trajectory3D, Vec3
from planetary_defense.domain.units import au_to_ld

#: Julian day of J2000.0 (2000-01-01 12:00).
J2000: Final = 2451545.0
#: Gaussian gravitational constant, radians per day (heliocentric, massless body).
GAUSSIAN_K: Final = 0.01720209895
_JD_OF_ORDINAL_ZERO: Final = 1721424.5  # date.toordinal() + this = JD at midnight


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def julian_day(moment: date | datetime) -> float:
    """Julian day of a date (at midnight) or of a timezone-aware datetime."""
    if isinstance(moment, datetime):
        offset = moment.utcoffset()
        if offset is None:
            raise ValueError("datetime must be timezone-aware (use UTC)")
        utc = moment - offset
        day_fraction = (utc.hour * 3600 + utc.minute * 60 + utc.second + utc.microsecond / 1e6) / 86400
        return utc.date().toordinal() + _JD_OF_ORDINAL_ZERO + day_fraction
    return moment.toordinal() + _JD_OF_ORDINAL_ZERO


def mean_motion(semi_major_axis_au: float) -> float:
    """Mean motion in radians per day (Kepler's third law)."""
    return GAUSSIAN_K / semi_major_axis_au**1.5


# --------------------------------------------------------------------------- #
# Kepler's equation and positions
# --------------------------------------------------------------------------- #


def solve_kepler(mean_anomaly: float, eccentricity: float, tol: float = 1e-13, max_iter: int = 50) -> float:
    """Eccentric anomaly E (radians) with ``E - e sin E = M``, for an elliptical orbit."""
    if not 0 <= eccentricity < 1:
        raise ValueError(f"eccentricity must be in [0, 1), got {eccentricity}")
    m = math.remainder(mean_anomaly, 2 * math.pi)  # in [-pi, pi]: Newton converges fast from here
    big_e = m if eccentricity < 0.8 else math.copysign(math.pi, m)
    for _ in range(max_iter):
        step = (big_e - eccentricity * math.sin(big_e) - m) / (1 - eccentricity * math.cos(big_e))
        big_e -= step
        if abs(step) < tol:
            return big_e
    raise ArithmeticError(f"Kepler's equation did not converge (M={mean_anomaly}, e={eccentricity})")


def _position_at_eccentric_anomaly(el: OrbitalElements, big_e: float) -> Vec3:
    a, e = el.semi_major_axis_au, el.eccentricity
    # In the orbital plane, x towards perihelion.
    x_orb = a * (math.cos(big_e) - e)
    y_orb = a * math.sqrt(1 - e * e) * math.sin(big_e)

    w, node, inc = (math.radians(d) for d in (el.perihelion_argument_deg, el.ascending_node_deg, el.inclination_deg))
    cos_w, sin_w, cos_n, sin_n, cos_i, sin_i = (
        math.cos(w), math.sin(w), math.cos(node), math.sin(node), math.cos(inc), math.sin(inc),
    )
    # Rotate by the argument of perihelion, the inclination and the ascending node: R_z(node) R_x(inc) R_z(w).
    return Vec3(
        (cos_n * cos_w - sin_n * sin_w * cos_i) * x_orb + (-cos_n * sin_w - sin_n * cos_w * cos_i) * y_orb,
        (sin_n * cos_w + cos_n * sin_w * cos_i) * x_orb + (-sin_n * sin_w + cos_n * cos_w * cos_i) * y_orb,
        (sin_w * sin_i) * x_orb + (cos_w * sin_i) * y_orb,
    )


def heliocentric_position(el: OrbitalElements, jd: float) -> Vec3:
    """Position (AU) of a body on orbit ``el`` at Julian day ``jd``, propagated as a two-body orbit."""
    mean_anomaly = math.radians(el.mean_anomaly_deg) + mean_motion(el.semi_major_axis_au) * (jd - el.epoch_jd)
    return _position_at_eccentric_anomaly(el, solve_kepler(mean_anomaly, el.eccentricity))


def orbit_path(el: OrbitalElements, samples: int = 360) -> list[Vec3]:
    """The whole ellipse as ``samples + 1`` points (the last repeats the first), evenly spaced in eccentric anomaly."""
    if samples < 3:
        raise ValueError("samples must be at least 3")
    return [_position_at_eccentric_anomaly(el, 2 * math.pi * k / samples) for k in range(samples + 1)]


# --------------------------------------------------------------------------- #
# Earth
# --------------------------------------------------------------------------- #

# Earth-Moon barycentre, J2000 mean elements and rates per Julian century (Standish, "Keplerian
# Elements for Approximate Positions of the Major Planets", JPL, valid 1800-2050):
#   (a AU, e, i deg, mean longitude L deg, longitude of perihelion varpi deg, ascending node deg)
_EARTH_J2000: Final = (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0)
_EARTH_RATES: Final = (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)


def earth_elements(jd: float) -> OrbitalElements:
    """Earth's mean orbit at ``jd`` (osculating there), following JPL's linear element rates."""
    centuries = (jd - J2000) / 36525
    a, e, inc, mean_long, peri_long, node = (
        value + rate * centuries for value, rate in zip(_EARTH_J2000, _EARTH_RATES, strict=True)
    )
    return OrbitalElements(
        semi_major_axis_au=a,
        eccentricity=e,
        inclination_deg=inc,
        ascending_node_deg=node,
        perihelion_argument_deg=peri_long - node,
        mean_anomaly_deg=mean_long - peri_long,
        epoch_jd=jd,
    )


#: Earth's mean orbit at J2000 (for building Earth-like test orbits).
EARTH: Final = earth_elements(J2000)


def earth_position(jd: float) -> Vec3:
    """Earth's heliocentric position (AU) at ``jd``, accurate to about 1e-4 AU within 1800-2050."""
    return heliocentric_position(earth_elements(jd), jd)


def geocentric_position(el: OrbitalElements, jd: float) -> Vec3:
    """Position (AU) of a body on orbit ``el`` relative to Earth at ``jd``."""
    body, earth = heliocentric_position(el, jd), earth_position(jd)
    return Vec3(body.x - earth.x, body.y - earth.y, body.z - earth.z)


# --------------------------------------------------------------------------- #
# Earth-centred close-approach trajectory
# --------------------------------------------------------------------------- #


def _norm(v: Vec3) -> float:
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def _geocentric_ld(el: OrbitalElements, jd: float) -> Vec3:
    p = geocentric_position(el, jd)
    return Vec3(au_to_ld(p.x), au_to_ld(p.y), au_to_ld(p.z))


def _refine_closest_time(el: OrbitalElements, lo: float, hi: float, iterations: int = 60) -> float:
    """Golden-section search for the time of minimum Earth distance in [lo, hi]."""
    ratio = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = _norm(_geocentric_ld(el, c)), _norm(_geocentric_ld(el, d))
    for _ in range(iterations):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = _norm(_geocentric_ld(el, c))
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = _norm(_geocentric_ld(el, d))
    return (a + b) / 2


def approach_trajectory(
    el: OrbitalElements,
    approach: date | datetime,
    miss_distance_ld: float,
    *,
    window_days: int = 3,
    steps_per_day: int = 24,
) -> Trajectory3D:
    """The path from ``window_days`` before to after ``approach``, centred on Earth, in LD.

    1. Sample the model's geocentric path and find its closest approach, refined between samples.
    2. Shift the whole path along that closest direction so the closest point sits exactly at
       NASA's ``miss_distance_ld``. A shift (not a scale) keeps the modelled speed and the
       direction of motion, and because motion is perpendicular to the line to Earth at closest
       approach, the shifted path's minimum distance is NASA's.
    """
    if not miss_distance_ld > 0:
        raise ValueError("miss_distance_ld must be positive")
    if window_days < 1 or steps_per_day < 1:
        raise ValueError("window_days and steps_per_day must be at least 1")

    start = approach - timedelta(days=window_days)
    jd0 = julian_day(start)
    times = [jd0 + k / steps_per_day for k in range(2 * window_days * steps_per_day + 1)]
    model = [_geocentric_ld(el, t) for t in times]

    nearest = min(range(len(model)), key=lambda k: _norm(model[k]))
    lo, hi = times[max(nearest - 1, 0)], times[min(nearest + 1, len(times) - 1)]
    t_closest = _refine_closest_time(el, lo, hi)
    closest_model = _geocentric_ld(el, t_closest)
    model_miss = _norm(closest_model)
    if model_miss == 0:
        raise ArithmeticError("the orbit model puts the asteroid at Earth's centre; no direction to draw")

    shift = (miss_distance_ld - model_miss) / model_miss  # move along the closest direction
    dx, dy, dz = closest_model.x * shift, closest_model.y * shift, closest_model.z * shift
    points = [Vec3(p.x + dx, p.y + dy, p.z + dz) for p in model]
    closest_point = Vec3(closest_model.x + dx, closest_model.y + dy, closest_model.z + dz)

    return Trajectory3D(
        times_jd=times,
        points_ld=points,
        closest_index=min(range(len(points)), key=lambda k: _norm(points[k])),
        closest_time_jd=t_closest,
        closest_point_ld=closest_point,
        miss_distance_ld=miss_distance_ld,
        model_miss_ld=model_miss,
    )
