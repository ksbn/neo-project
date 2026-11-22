"""NeoWs payload validation and mapping to domain observations (infrastructure/nasa_dto.py). No I/O."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from planetary_defense.domain.errors import FeedContractError
from planetary_defense.infrastructure.nasa_dto import parse_feed

from support import make_feed, make_neo


def test_parses_nested_dates_and_coerces_numeric_strings():
    result = parse_feed(make_feed({"2026-11-10": [make_neo("1")], "2026-11-11": [make_neo("2", "2026-11-11")]}))

    assert {o.neo_reference_id for o in result.observations} == {"1", "2"}
    obs = next(o for o in result.observations if o.neo_reference_id == "1")
    assert obs.approach is not None
    assert obs.approach.relative_velocity_km_s == Decimal("12.345678")
    assert obs.approach.miss_distance_km == Decimal("4567890.123")
    assert obs.approach_date == date(2026, 11, 10)
    assert obs.asteroid.estimated_diameter_max_km == Decimal("0.25")
    assert result.feed_dates == {date(2026, 11, 10), date(2026, 11, 11)}


def test_missing_optional_fields_become_none():
    neo = make_neo("1")
    del neo["estimated_diameter"]
    del neo["nasa_jpl_url"]
    neo["close_approach_data"] = []

    (obs,) = parse_feed(make_feed({"2026-11-10": [neo]})).observations

    assert obs.asteroid.estimated_diameter_min_km is None
    assert obs.asteroid.nasa_jpl_url is None
    assert obs.approach is None and obs.approach_date is None


def test_bad_object_is_skipped_not_fatal():
    result = parse_feed(make_feed({"2026-11-10": [make_neo("1"), make_neo("2", name=None), "garbage"]}))

    assert [o.neo_reference_id for o in result.observations] == ["1"]
    assert {i.neo_reference_id for i in result.invalid} == {"2", None}


def test_diameter_min_above_max_rejected():
    neo = make_neo("1", estimated_diameter={"kilometers": {"estimated_diameter_min": 5, "estimated_diameter_max": 1}})
    result = parse_feed(make_feed({"2026-11-10": [neo]}))
    assert not result.observations
    assert "greater than" in result.invalid[0].error


def test_duplicate_asteroid_keeps_every_appearance():
    result = parse_feed(make_feed({"2026-11-12": [make_neo("1", "2026-11-12")], "2026-11-10": [make_neo("1")]}))
    # The parser keeps every appearance, in feed-date order; choosing is the domain's job.
    assert [o.approach_date for o in result.observations] == [date(2026, 11, 10), date(2026, 11, 12)]
    assert result.unique_asteroids == 1


def test_empty_feed():
    result = parse_feed({"element_count": 0, "near_earth_objects": {}})
    assert result.observations == [] and result.invalid == [] and result.feed_dates == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"element_count": 1},
        {"element_count": 0, "near_earth_objects": {"not-a-date": []}},
        {"element_count": -1, "near_earth_objects": {}},
    ],
)
def test_malformed_envelope_raises_contract_error(payload):
    with pytest.raises(FeedContractError) as exc:
        parse_feed(payload)
    assert isinstance(exc.value.__cause__, ValidationError)  # original kept for the traceback


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("absolute_magnitude_h", 999.999, True),
        ("absolute_magnitude_h", 999.9995, False),  # rounds to 1000.000: overflows NUMERIC(6, 3)
        ("absolute_magnitude_h", -1234.5, False),
        ("miss_distance", "99999999999999999.999", True),  # largest value NUMERIC(20, 3) holds
        ("miss_distance", "99999999999999999.9995", False),  # rounds up to 18 integer digits
        ("miss_distance", "100000000000000000", False),
    ],
)
def test_values_that_overflow_db_columns_are_rejected_per_record(field, value, accepted):
    if field == "miss_distance":
        neo = make_neo("1")
        neo["close_approach_data"][0]["miss_distance"]["kilometers"] = value
    else:
        neo = make_neo("1", **{field: value})

    result = parse_feed(make_feed({"2026-11-10": [neo, make_neo("2")]}))

    ids = {o.neo_reference_id for o in result.observations}
    assert ids == ({"1", "2"} if accepted else {"2"})  # the other record is never affected
