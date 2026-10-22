from datetime import UTC, datetime, timedelta

import pytest

from scripts.observation_time import rnmca_hourly_interval


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
