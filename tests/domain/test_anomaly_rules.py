"""Anomaly rules (domain/anomaly_rules.py): small composable functions over observations. No I/O."""

from datetime import date
from decimal import Decimal

import pytest

from planetary_defense.domain.anomaly_rules import (
    AlertThresholds,
    all_of,
    default_rules,
    evaluate,
    high_velocity,
    large_diameter,
    miss_distance,
    potentially_hazardous,
)
from planetary_defense.domain.models import AnomalyAlert, Asteroid, CloseApproach, Observation, Severity
from planetary_defense.domain.units import LUNAR_DISTANCE_KM


def obs(
    ref: str = "1",
    *,
    ld: float | None = 50.0,
    km_s: float = 10.0,
    diameter_km: float | None = 0.1,
    hazardous: bool = False,
    day: date = date(2026, 11, 22),
) -> Observation:
    asteroid = Asteroid(
        neo_reference_id=ref, name=f"({ref})", is_potentially_hazardous=hazardous,
        estimated_diameter_min_km=None if diameter_km is None else Decimal(str(diameter_km / 2)),
        estimated_diameter_max_km=None if diameter_km is None else Decimal(str(diameter_km)),
    )
    approach = None if ld is None else CloseApproach(
        close_approach_date=day, relative_velocity_km_s=Decimal(str(km_s)),
        miss_distance_km=Decimal(str(ld * LUNAR_DISTANCE_KM)), orbiting_body="Earth",
    )
    return Observation(asteroid=asteroid, approach=approach)


# --------------------------------------------------------------------------- #
# Single rules
# --------------------------------------------------------------------------- #

TIERS = miss_distance([(1.0, Severity.CRITICAL), (5.0, Severity.WARNING), (10.0, Severity.WATCH)])


@pytest.mark.parametrize(
    ("ld", "expected"),
    [(0.5, Severity.CRITICAL), (1.0, Severity.CRITICAL), (3.0, Severity.WARNING), (9.9, Severity.WATCH), (10.1, None)],
)
def test_miss_distance_picks_the_tightest_tier(ld, expected):
    alert = TIERS(obs(ld=ld))
    assert (alert.severity if alert else None) == expected
    if alert:
        assert alert.rule == "close_approach"
        assert alert.value == pytest.approx(ld)
        assert f"{ld:.2f} LD" in alert.message


def test_inside_the_moons_orbit_is_called_out():
    alert = TIERS(obs(ld=0.4))
    assert alert is not None and "inside the Moon's orbit" in alert.message


def test_tiers_can_be_given_in_any_order():
    shuffled = miss_distance([(10.0, Severity.WATCH), (1.0, Severity.CRITICAL)])
    alert = shuffled(obs(ld=0.5))
    assert alert is not None and alert.severity is Severity.CRITICAL


def test_distance_rules_ignore_observations_without_an_approach():
    assert TIERS(obs(ld=None)) is None
    assert high_velocity(30.0)(obs(ld=None)) is None


def test_high_velocity():
    rule = high_velocity(30.0)
    fast = rule(obs(km_s=31.5))
    assert fast is not None and fast.severity is Severity.WATCH and fast.value == pytest.approx(31.5)
    assert rule(obs(km_s=29.9)) is None


def test_large_diameter_uses_the_largest_estimate():
    rule = large_diameter(1.0)
    big = rule(obs(diameter_km=1.66))
    assert big is not None and big.rule == "large_diameter" and big.value == pytest.approx(1.66)
    assert rule(obs(diameter_km=0.99)) is None
    assert rule(obs(diameter_km=None)) is None


def test_potentially_hazardous_flag():
    alert = potentially_hazardous()(obs(hazardous=True))
    assert alert is not None and alert.severity is Severity.INFO and alert.value is None
    assert potentially_hazardous()(obs(hazardous=False)) is None


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #

HAZARDOUS_AND_CLOSE = all_of(
    "hazardous_close_pass", Severity.WARNING, "Potentially hazardous asteroid passing close",
    potentially_hazardous(), miss_distance([(20.0, Severity.WATCH)]),
)


def test_all_of_fires_only_when_every_part_fires():
    alert = HAZARDOUS_AND_CLOSE(obs(hazardous=True, ld=15.0))
    assert alert is not None
    assert (alert.rule, alert.severity) == ("hazardous_close_pass", Severity.WARNING)
    assert "15.00 LD" in alert.message  # details of the parts are kept

    assert HAZARDOUS_AND_CLOSE(obs(hazardous=False, ld=15.0)) is None
    assert HAZARDOUS_AND_CLOSE(obs(hazardous=True, ld=25.0)) is None


def test_all_of_needs_at_least_one_rule():
    with pytest.raises(ValueError):
        all_of("empty", Severity.INFO, "nothing")


def test_any_callable_is_a_rule():
    def named_after_apophis(o: Observation) -> AnomalyAlert | None:
        if "Apophis" not in o.asteroid.name:
            return None
        return AnomalyAlert.for_observation(o, "apophis", Severity.INFO, "It's Apophis")

    apophis = obs(ref="99942")
    apophis = apophis.model_copy(update={"asteroid": apophis.asteroid.model_copy(update={"name": "99942 Apophis"})})
    (alert,) = evaluate([apophis, obs()], [named_after_apophis])
    assert alert.rule == "apophis" and alert.neo_reference_id == "99942"


# --------------------------------------------------------------------------- #
# Evaluation and defaults
# --------------------------------------------------------------------------- #


def test_evaluate_orders_by_severity_then_date_then_rule():
    early, late = date(2026, 11, 20), date(2026, 11, 25)
    alerts = evaluate(
        [obs("a", ld=0.5, day=late), obs("b", ld=3.0, km_s=40.0, day=early), obs("c", ld=0.8, day=early)],
        [TIERS, high_velocity(30.0)],
    )
    assert [(a.neo_reference_id, a.rule) for a in alerts] == [
        ("c", "close_approach"),   # critical, earlier
        ("a", "close_approach"),   # critical, later
        ("b", "close_approach"),   # warning
        ("b", "high_velocity"),    # watch
    ]
    assert alerts[0].close_approach_date == early and alerts[0].name == "(c)"


def test_default_rules_with_default_thresholds():
    t = AlertThresholds()
    assert (t.critical_ld, t.warning_ld, t.watch_ld) == (1.0, 5.0, 10.0)
    assert (t.high_velocity_km_s, t.large_diameter_km, t.hazardous_close_ld) == (30.0, 1.0, 20.0)

    rules = default_rules()
    quiet = evaluate([obs(ld=50.0, km_s=10.0, diameter_km=0.1, hazardous=False)], rules)
    assert quiet == []

    loud = evaluate([obs(ld=0.9, km_s=35.0, diameter_km=1.2, hazardous=True)], rules)
    assert [(a.rule, a.severity) for a in loud] == [
        ("close_approach", Severity.CRITICAL),
        ("hazardous_close_pass", Severity.WARNING),
        ("high_velocity", Severity.WATCH),
        ("large_diameter", Severity.WATCH),
        ("potentially_hazardous", Severity.INFO),
    ]


def test_thresholds_are_configurable():
    strict = default_rules(AlertThresholds(watch_ld=60.0, high_velocity_km_s=5.0))
    rules_hit = {a.rule for a in evaluate([obs(ld=50.0, km_s=10.0)], strict)}
    assert rules_hit == {"close_approach", "high_velocity"}


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="critical_ld"):
        AlertThresholds(critical_ld=6.0, warning_ld=5.0)
