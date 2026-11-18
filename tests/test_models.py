"""Validation and flattening of NeoWs payloads (models.py). No network or database."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from conftest import make_feed, make_neo
from models import parse_feed


def test_parses_nested_dates_and_coerces_numeric_strings():
    result = parse_feed(make_feed({"2026-11-10": [make_neo("1")], "2026-11-11": [make_neo("2", "2026-11-11")]}))

    assert {r.neo_reference_id for r in result.records} == {"1", "2"}
    rec = next(r for r in result.records if r.neo_reference_id == "1")
    assert rec.relative_velocity_km_s == Decimal("12.345678")
    assert rec.miss_distance_km == Decimal("4567890.123")
    assert rec.close_approach_date == date(2026, 11, 10)
    assert result.feed_dates == {date(2026, 11, 10), date(2026, 11, 11)}


def test_missing_optional_fields_become_none():
    neo = make_neo("1")
    del neo["estimated_diameter"]
    del neo["nasa_jpl_url"]
    neo["close_approach_data"] = []

    (rec,) = parse_feed(make_feed({"2026-11-10": [neo]})).records

    assert rec.estimated_diameter_min_km is None
    assert rec.nasa_jpl_url is None
    assert rec.close_approach_date is None
    assert rec.miss_distance_km is None


def test_bad_object_is_skipped_not_fatal():
    result = parse_feed(make_feed({"2026-11-10": [make_neo("1"), make_neo("2", name=None), "garbage"]}))

    assert [r.neo_reference_id for r in result.records] == ["1"]
    assert {i.neo_reference_id for i in result.invalid} == {"2", None}


def test_diameter_min_above_max_rejected():
    neo = make_neo("1", estimated_diameter={"kilometers": {"estimated_diameter_min": 5, "estimated_diameter_max": 1}})
    result = parse_feed(make_feed({"2026-11-10": [neo]}))
    assert not result.records
    assert "greater than" in result.invalid[0].error


def test_duplicate_asteroid_keeps_latest_approach():
    result = parse_feed(make_feed({"2026-11-12": [make_neo("1", "2026-11-12")], "2026-11-10": [make_neo("1")]}))
    (rec,) = result.records
    assert rec.close_approach_date == date(2026, 11, 12)


def test_empty_feed():
    result = parse_feed({"element_count": 0, "near_earth_objects": {}})
    assert result.records == [] and result.invalid == [] and result.feed_dates == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"element_count": 1},
        {"element_count": 0, "near_earth_objects": {"not-a-date": []}},
        {"element_count": -1, "near_earth_objects": {}},
    ],
)
def test_malformed_envelope_raises(payload):
    with pytest.raises(ValidationError):
        parse_feed(payload)


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

    ids = {r.neo_reference_id for r in result.records}
    assert ids == ({"1", "2"} if accepted else {"2"})  # the other record is never affected
