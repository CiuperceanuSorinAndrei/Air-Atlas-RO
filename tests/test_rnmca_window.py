from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from scripts.import_rnmca import missing_rnmca_hourly_intervals

START = datetime(2026, 10, 10, 8, tzinfo=UTC)
HOUR = timedelta(hours=1)


def interval(start, hours=1):
    return {
        "observedFrom": start.isoformat(),
        "observedTo": (start + hours * HOUR).isoformat(),
    }


def test_missing_intervals_include_leading_internal_and_trailing_gaps():
    observations = [interval(START + 3 * HOUR), interval(START + HOUR)]
    original = deepcopy(observations)

    missing = missing_rnmca_hourly_intervals(
        observations, window_start=START, window_end=START + 5 * HOUR
    )

    assert missing == [interval(START + i * HOUR) for i in (0, 2, 4)]
    assert observations == original


def test_empty_observations_leave_the_entire_requested_window_missing():
    assert missing_rnmca_hourly_intervals(
        [], window_start=START, window_end=START + 2 * HOUR
    ) == [interval(START), interval(START + HOUR)]


def test_complete_coverage_with_duplicates_has_no_missing_intervals():
    assert (
        missing_rnmca_hourly_intervals(
            [interval(START + HOUR), interval(START), interval(START)],
            window_start=START,
            window_end=START + 2 * HOUR,
        )
        == []
    )


def test_equivalent_offsets_are_compared_as_physical_utc_hours():
    local = START.astimezone(timezone(timedelta(hours=3)))

    assert missing_rnmca_hourly_intervals(
        [interval(local)], window_start=local, window_end=local + 2 * HOUR
    ) == [interval(START + HOUR)]


@pytest.mark.parametrize(
    ("start", "end", "expected_hours"),
    [
        ("2026-03-29T00:00:00+02:00", "2026-03-30T00:00:00+03:00", 23),
        ("2025-10-26T00:00:00+03:00", "2025-10-27T00:00:00+02:00", 25),
    ],
)
def test_dst_days_have_their_physical_number_of_hours(start, end, expected_hours):
    missing = missing_rnmca_hourly_intervals(
        [],
        window_start=datetime.fromisoformat(start),
        window_end=datetime.fromisoformat(end),
    )

    assert len(missing) == expected_hours
    assert len({item["observedFrom"] for item in missing}) == expected_hours
    assert (
        missing[0]["observedFrom"]
        == datetime.fromisoformat(start).astimezone(UTC).isoformat()
    )
    assert (
        missing[-1]["observedTo"]
        == datetime.fromisoformat(end).astimezone(UTC).isoformat()
    )


@pytest.mark.parametrize("name", ["window_start", "window_end"])
@pytest.mark.parametrize(
    ("bad", "error", "message"),
    [
        ("2026-10-10", TypeError, "must be a datetime"),
        (
            datetime(2026, 10, 10, tzinfo=UTC).replace(tzinfo=None),
            ValueError,
            "must include its UTC offset",
        ),
        (START + timedelta(minutes=1), ValueError, "must align to a UTC hour"),
    ],
)
def test_invalid_window_boundaries_are_errors(name, bad, error, message):
    arguments = {"window_start": START, "window_end": START + HOUR}
    arguments[name] = bad

    with pytest.raises(error, match=message):
        missing_rnmca_hourly_intervals([], **arguments)


@pytest.mark.parametrize("end", [START, START - HOUR])
def test_window_must_have_positive_duration(end):
    with pytest.raises(ValueError, match="window_end must be after window_start"):
        missing_rnmca_hourly_intervals([], window_start=START, window_end=end)


@pytest.mark.parametrize("offset", [-1, 2])
def test_out_of_window_observations_are_errors(offset):
    with pytest.raises(ValueError, match="outside the requested window"):
        missing_rnmca_hourly_intervals(
            [interval(START + offset * HOUR)],
            window_start=START,
            window_end=START + 2 * HOUR,
        )


@pytest.mark.parametrize("hours", [0, -1, 2])
def test_non_hourly_observation_intervals_are_errors(hours):
    with pytest.raises(ValueError, match="must span exactly one UTC hour"):
        missing_rnmca_hourly_intervals(
            [interval(START, hours)], window_start=START, window_end=START + 3 * HOUR
        )


@pytest.mark.parametrize("field", ["observedFrom", "observedTo"])
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2026-10-10T08:00:00", "must include its UTC offset"),
        ("2026-10-10T08:00:00.001+00:00", "must align to a UTC hour"),
    ],
)
def test_observation_boundaries_must_be_aware_utc_hours(field, value, message):
    observation = interval(START)
    observation[field] = value

    with pytest.raises(ValueError, match=message):
        missing_rnmca_hourly_intervals(
            [observation], window_start=START, window_end=START + HOUR
        )
