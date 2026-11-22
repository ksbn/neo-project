"""Domain rules (domain/models.py): no I/O."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from planetary_defense.domain.models import Asteroid, CloseApproach, FeedBatch, Observation, date_windows, latest


def obs(ref: str, day: str | None, name: str = "x") -> Observation:
    approach = (
        CloseApproach(
            close_approach_date=date.fromisoformat(day),
            relative_velocity_km_s=Decimal(1),
            miss_distance_km=Decimal(1),
            orbiting_body="Earth",
        )
        if day
        else None
    )
    asteroid = Asteroid(neo_reference_id=ref, name=name, is_potentially_hazardous=False)
    return Observation(asteroid=asteroid, approach=approach)


def test_latest_keeps_the_newest_approach_per_asteroid():
    result = latest([obs("1", "2026-11-12", "new"), obs("2", "2026-11-01"), obs("1", "2026-11-10", "old")])
    assert [(o.neo_reference_id, o.asteroid.name) for o in result] == [("1", "new"), ("2", "x")]


def test_an_approach_beats_no_approach_and_ties_go_to_the_later_one():
    assert latest([obs("1", "2026-11-10"), obs("1", None)])[0].approach_date == date(2026, 11, 10)
    assert latest([obs("1", None), obs("1", "2026-11-10")])[0].approach_date == date(2026, 11, 10)
    assert latest([obs("1", "2026-11-10", "a"), obs("1", "2026-11-10", "b")])[0].asteroid.name == "b"


def test_feed_batch_counts_unique_asteroids():
    batch = FeedBatch(observations=[obs("1", "2026-11-10"), obs("1", "2026-11-11"), obs("2", None)])
    assert batch.unique_asteroids == 2


def test_entities_reject_unknown_fields():
    """The domain is strict, unlike the lenient NASA DTOs."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        Asteroid(neo_reference_id="1", name="x", is_potentially_hazardous=False, typo_field=1)  # pyright: ignore[reportCallIssue]


def test_date_windows_split_long_ranges():
    assert list(date_windows(date(2026, 11, 1), date(2026, 11, 15), 7)) == [
        (date(2026, 11, 1), date(2026, 11, 7)),
        (date(2026, 11, 8), date(2026, 11, 14)),
        (date(2026, 11, 15), date(2026, 11, 15)),
    ]


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [(date(2026, 11, 2), date(2026, 11, 1), 7), (date(2026, 11, 1), date(2026, 11, 1), 0)],
)
def test_date_windows_reject_bad_input(start, end, days):
    with pytest.raises(ValueError):
        list(date_windows(start, end, days))
