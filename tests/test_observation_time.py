from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scripts.observation_time import bucharest_utc_candidates, rnmca_hourly_interval


@pytest.mark.parametrize(
    ("timestamp_ms", "expected_start", "expected_end"),
    [
        (1791658800000, "2026-10-10T18:00:00Z", "2026-10-10T19:00:00Z"),
        (
            1791658800123,
            "2026-10-10T18:00:00.123000Z",
            "2026-10-10T19:00:00.123000Z",
        ),
        (1768503600000, "2026-01-15T18:00:00Z", "2026-01-15T19:00:00Z"),
        (1784142000000, "2026-07-15T18:00:00Z", "2026-07-15T19:00:00Z"),
        (1774746000000, "2026-03-29T00:00:00Z", "2026-03-29T01:00:00Z"),
        (1761436800000, "2025-10-25T23:00:00Z", "2025-10-26T00:00:00Z"),
        (1761440400000, "2025-10-26T00:00:00Z", "2025-10-26T01:00:00Z"),
        (1709251200000, "2024-02-29T23:00:00Z", "2024-03-01T00:00:00Z"),
        (1767225600000, "2025-12-31T23:00:00Z", "2026-01-01T00:00:00Z"),
    ],
    ids=[
        "captured-dj1-no2",
        "milliseconds-preserved",
        "winter",
        "summer",
        "spring-transition",
        "autumn-first-occurrence",
        "autumn-second-occurrence",
        "leap-day-month-boundary",
        "year-boundary",
    ],
)
def test_rnmca_hourly_interval_returns_expected_utc_interval(
    timestamp_ms, expected_start, expected_end
):
    result = rnmca_hourly_interval(timestamp_ms)

    assert isinstance(result, tuple)
    assert result == (
        datetime.fromisoformat(expected_start),
        datetime.fromisoformat(expected_end),
    )
    start, end = result
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC
    assert end - start == timedelta(hours=1)


@pytest.mark.parametrize(
    "value",
    ["1791658800000", 1791658800000.0, True, False, None, b"1791658800000", [], {}],
    ids=["text", "float", "true", "false", "none", "bytes", "list", "dict"],
)
def test_rnmca_hourly_interval_rejects_non_integer_input(value):
    with pytest.raises(TypeError, match="timestamp_ms must be an integer"):
        rnmca_hourly_interval(value)


@pytest.mark.parametrize(
    ("timestamp_ms", "expected_start", "expected_end"),
    [
        (-1, "1969-12-31T22:59:59.999000Z", "1969-12-31T23:59:59.999000Z"),
        (16725268800123, "2500-01-01T11:00:00.123000Z", "2500-01-01T12:00:00.123000Z"),
        (253370808000123, "9999-01-01T11:00:00.123000Z", "9999-01-01T12:00:00.123000Z"),
    ],
)
def test_epoch_milliseconds_are_exact_without_float_rounding(
    timestamp_ms, expected_start, expected_end
):
    assert rnmca_hourly_interval(timestamp_ms) == (
        datetime.fromisoformat(expected_start),
        datetime.fromisoformat(expected_end),
    )


@pytest.mark.parametrize("timestamp_ms", [-62135596800000, 253402300800000, 10**100])
def test_unrepresentable_complete_intervals_are_explicit_errors(timestamp_ms):
    with pytest.raises(ValueError, match="complete hourly interval"):
        rnmca_hourly_interval(timestamp_ms)


@pytest.mark.parametrize("value", [None, "2026-10-10T22:00:00", 0, True, [], {}])
def test_bucharest_utc_candidates_rejects_non_datetime_input(value):
    with pytest.raises(TypeError, match="^local_time must be a datetime$"):
        bucharest_utc_candidates(value)


@pytest.mark.parametrize(
    "offset", [timedelta(0), timedelta(hours=2), timedelta(hours=-5)]
)
def test_bucharest_utc_candidates_rejects_timezone_aware_input(offset):
    value = datetime(2026, 10, 10, 22, tzinfo=timezone(offset))
    with pytest.raises(ValueError, match="^local_time must be timezone-naive$"):
        bucharest_utc_candidates(value)


@pytest.mark.parametrize(
    ("local_time", "expected"),
    [
        (
            datetime.fromisoformat("2026-10-10T22:00:00"),
            datetime(2026, 10, 10, 19, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-01-15T22:00:00"),
            datetime(2026, 1, 15, 20, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-07-15T22:00:00"),
            datetime(2026, 7, 15, 19, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-01-01T00:30:00"),
            datetime(2025, 12, 31, 22, 30, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2024-03-01T00:30:00"),
            datetime(2024, 2, 29, 22, 30, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-03-29T02:30:00"),
            datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-03-29T04:00:00"),
            datetime(2026, 3, 29, 1, tzinfo=UTC),
        ),
        (
            datetime.fromisoformat("2026-10-10T22:17:31.123456"),
            datetime(2026, 10, 10, 19, 17, 31, 123456, tzinfo=UTC),
        ),
    ],
)
def test_bucharest_utc_candidates_converts_unambiguous_labels(local_time, expected):
    original = (local_time.isoformat(), local_time.tzinfo, local_time.fold)

    result = bucharest_utc_candidates(local_time)

    assert isinstance(result, tuple)
    assert result == (expected,)
    assert result[0].tzinfo is UTC
    assert (local_time.isoformat(), local_time.tzinfo, local_time.fold) == original


@pytest.mark.parametrize(
    "local_time",
    [
        datetime.fromisoformat("2026-03-29T03:00:00"),
        datetime.fromisoformat("2026-03-29T03:30:00"),
        datetime.fromisoformat("2026-03-29T03:59:59.999999"),
    ],
)
def test_bucharest_utc_candidates_excludes_nonexistent_local_labels(local_time):
    original = (local_time.isoformat(), local_time.tzinfo, local_time.fold)

    assert bucharest_utc_candidates(local_time) == ()
    assert (local_time.isoformat(), local_time.tzinfo, local_time.fold) == original


@pytest.mark.parametrize("fold", [0, 1])
def test_bucharest_candidates_preserves_both_autumn_occurrences(fold):
    value = datetime.fromisoformat("2025-10-26T03:30:00").replace(fold=fold)
    assert bucharest_utc_candidates(value) == (
        datetime(2025, 10, 26, 0, 30, tzinfo=UTC),
        datetime(2025, 10, 26, 1, 30, tzinfo=UTC),
    )


@pytest.mark.parametrize("fold", [0, 1])
def test_bucharest_candidates_deduplicates_ordinary_hour(fold):
    value = datetime.fromisoformat("2026-01-15T22:00:00").replace(fold=fold)
    assert bucharest_utc_candidates(value) == (datetime(2026, 1, 15, 20, tzinfo=UTC),)


@pytest.mark.parametrize(
    "value",
    [
        datetime.fromisoformat("0001-01-01T00:00:00"),
        datetime.fromisoformat("9999-12-31T23:59:59.999999"),
    ],
)
def test_bucharest_candidates_has_explicit_range_errors_or_exact_results(value):
    if value == datetime.fromisoformat("0001-01-01T00:00:00"):
        with pytest.raises(ValueError, match="cannot represent its UTC candidates"):
            bucharest_utc_candidates(value)
    else:
        assert bucharest_utc_candidates(value) == (
            datetime(9999, 12, 31, 21, 59, 59, 999999, tzinfo=UTC),
        )


def test_every_2025_and_2026_utc_hour_survives_local_candidate_reconstruction():
    zone = ZoneInfo("Europe/Bucharest")
    instant = datetime(2025, 1, 1, tzinfo=UTC)
    stop = datetime(2027, 1, 1, tzinfo=UTC)
    ambiguous_instants = 0
    checked = 0
    while instant < stop:
        label = instant.astimezone(zone).replace(tzinfo=None)
        candidates = bucharest_utc_candidates(label)
        assert instant in candidates
        assert candidates == tuple(sorted(set(candidates)))
        assert all(candidate.tzinfo is UTC for candidate in candidates)
        assert len(candidates) in (1, 2)
        ambiguous_instants += len(candidates) == 2
        checked += 1
        instant += timedelta(hours=1)
    assert checked == 17520
    assert ambiguous_instants == 4
