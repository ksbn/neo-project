"""Anomaly rules for the Control Room. Pure functions, composed rather than inherited.

A rule is any callable ``Observation -> AnomalyAlert | None``. The factories
below build the common ones from thresholds; :func:`all_of` combines rules into
a stricter one; :func:`evaluate` runs a set of rules over many observations.
New rules need no base class: write a function with that signature.

Distances are in lunar distances (LD, NeoWs convention, see ``domain/units.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from planetary_defense.domain.models import AnomalyAlert, Observation, Severity
from planetary_defense.domain.units import km_to_ld

Rule = Callable[[Observation], AnomalyAlert | None]


# --------------------------------------------------------------------------- #
# Rule factories
# --------------------------------------------------------------------------- #


def miss_distance(tiers: Sequence[tuple[float, Severity]], rule: str = "close_approach") -> Rule:
    """Fires when the pass is within a tier's distance (LD); the tightest matching tier wins."""
    # Nearest threshold first; on equal thresholds the more severe tier wins.
    ordered = sorted(tiers, key=lambda tier: (tier[0], -tier[1]))

    def check(obs: Observation) -> AnomalyAlert | None:
        if obs.approach is None:
            return None
        ld = km_to_ld(float(obs.approach.miss_distance_km))
        for max_ld, severity in ordered:
            if ld <= max_ld:
                where = " (inside the Moon's orbit)" if ld < 1 else ""
                message = f"Passes {ld:.2f} LD from Earth{where}, within {max_ld:g} LD"
                return AnomalyAlert.for_observation(obs, rule, severity, message, value=ld, threshold=max_ld)
        return None

    return check


def high_velocity(min_km_s: float, severity: Severity = Severity.WATCH) -> Rule:
    """Fires when the relative velocity at the pass is at least ``min_km_s``."""

    def check(obs: Observation) -> AnomalyAlert | None:
        if obs.approach is None:
            return None
        km_s = float(obs.approach.relative_velocity_km_s)
        if km_s < min_km_s:
            return None
        message = f"Relative velocity {km_s:.1f} km/s (at least {min_km_s:g} km/s)"
        return AnomalyAlert.for_observation(obs, "high_velocity", severity, message, value=km_s, threshold=min_km_s)

    return check


def large_diameter(min_km: float, severity: Severity = Severity.WATCH) -> Rule:
    """Fires when the largest diameter estimate is at least ``min_km``."""

    def check(obs: Observation) -> AnomalyAlert | None:
        diameter = obs.asteroid.estimated_diameter_max_km
        if diameter is None or float(diameter) < min_km:
            return None
        km = float(diameter)
        message = f"Estimated up to {km:.2f} km across (at least {min_km:g} km)"
        return AnomalyAlert.for_observation(obs, "large_diameter", severity, message, value=km, threshold=min_km)

    return check


def potentially_hazardous(severity: Severity = Severity.INFO) -> Rule:
    """Fires for asteroids NASA classifies as potentially hazardous."""

    def check(obs: Observation) -> AnomalyAlert | None:
        if not obs.asteroid.is_potentially_hazardous:
            return None
        return AnomalyAlert.for_observation(
            obs, "potentially_hazardous", severity, "Classified by NASA as a potentially hazardous asteroid"
        )

    return check


def all_of(rule: str, severity: Severity, title: str, *rules: Rule) -> Rule:
    """A rule that fires only when every one of ``rules`` fires.

    Its message is ``title`` plus the details of the parts that measured something
    (flag-only parts are already implied by the title).
    """
    if not rules:
        raise ValueError("all_of needs at least one rule")

    def check(obs: Observation) -> AnomalyAlert | None:
        parts: list[AnomalyAlert] = []
        for inner in rules:
            alert = inner(obs)
            if alert is None:
                return None
            parts.append(alert)
        details = [p.message for p in parts if p.value is not None]
        message = f"{title}: " + "; ".join(details) if details else title
        return AnomalyAlert.for_observation(obs, rule, severity, message)

    return check


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def evaluate(observations: Iterable[Observation], rules: Iterable[Rule]) -> list[AnomalyAlert]:
    """Every alert the rules raise, most urgent first, then by approach date and rule name."""
    rules = tuple(rules)
    alerts = [alert for obs in observations for rule in rules if (alert := rule(obs)) is not None]
    return sorted(
        alerts,
        key=lambda a: (-a.severity, a.close_approach_date is None, a.close_approach_date, a.rule, a.neo_reference_id),
    )


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AlertThresholds:
    """Tunable thresholds for :func:`default_rules`.

    Defaults were chosen against the stored data (closest pass 9.7 LD, fastest
    38 km/s, largest 1.7 km): alerts stay rare enough to mean something.
    """

    critical_ld: float = 1.0  # inside the Moon's orbit
    warning_ld: float = 5.0
    watch_ld: float = 10.0
    high_velocity_km_s: float = 30.0
    large_diameter_km: float = 1.0
    hazardous_close_ld: float = 20.0  # a potentially hazardous asteroid this close is a warning

    def __post_init__(self) -> None:
        if not 0 < self.critical_ld <= self.warning_ld <= self.watch_ld:
            raise ValueError("thresholds must satisfy 0 < critical_ld <= warning_ld <= watch_ld")


def default_rules(t: AlertThresholds | None = None) -> tuple[Rule, ...]:
    t = t or AlertThresholds()
    return (
        miss_distance([(t.critical_ld, Severity.CRITICAL), (t.warning_ld, Severity.WARNING),
                       (t.watch_ld, Severity.WATCH)]),
        all_of(
            "hazardous_close_pass", Severity.WARNING, "Potentially hazardous asteroid passing close",
            potentially_hazardous(), miss_distance([(t.hazardous_close_ld, Severity.WATCH)]),
        ),
        high_velocity(t.high_velocity_km_s),
        large_diameter(t.large_diameter_km),
        potentially_hazardous(),
    )
