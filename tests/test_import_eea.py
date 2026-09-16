import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow
import pytest

import scripts.import_eea as importer
from scripts.import_eea import (
    NoValidObservationsError,
    normalize_observation,
    select_latest_valid_observation,
)


def test_selects_latest_valid_observation_when_newest_is_invalid() -> None:
    table = pyarrow.table(
        {
            "End": [
                datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC),
            ],
            "Validity": [1, -1],
            "Value": [20, 99],
        }
    )
    selected_observation = select_latest_valid_observation(table)
    assert selected_observation["Value"] == 20


def test_raises_when_no_valid_observations_exist() -> None:
    table = pyarrow.table(
        {
            "End": [
                datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC),
            ],
            "Validity": [-1, -99],
        }
    )
    with pytest.raises(NoValidObservationsError, match="No valid observations found"):
        select_latest_valid_observation(table)


def test_raises_when_latest_valid_timestamp_is_not_unique() -> None:
    latest_end = datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC)
    table = pyarrow.table(
        {
            "End": [latest_end, latest_end],
            "Validity": [1, 2],
        }
    )
    with pytest.raises(ValueError, match="Latest observation timestamp is not unique"):
        select_latest_valid_observation(table)


@pytest.mark.parametrize(
    ("verification", "expected_status"),
    [(1, "validated"), (2, "preliminary"), (3, "preliminary")],
)
def test_normalize_observation_maps_verification_to_status(
    verification: int, expected_status: str
) -> None:
    observed_from = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
    observed_to = datetime(2026, 3, 28, 11, 0, 0, tzinfo=UTC)
    raw_observation = {
        "Value": 20,
        "Start": observed_from,
        "End": observed_to,
        "ResultTime": observed_to,
        "Pollutant": 8,
        "Samplingpoint": "RO/SPO-RO0080A_00008_100",
        "Unit": "ug.m-3",
        "Validity": 1,
        "Verification": verification,
    }
    station_metadata = {
        "stationId": "RO0080A",
        "stationName": "DJ-3",
        "longitude": 23.7787,
        "latitude": 44.3268,
    }

    normalized_observation = normalize_observation(
        raw_observation,
        station_metadata,
        "https://example.com/observation.parquet",
    )

    assert normalized_observation["status"] == expected_status


def test_normalize_observation_rejects_unknown_verification() -> None:
    observed_at = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
    raw_observation = {
        "Value": 20,
        "Start": observed_at,
        "End": observed_at,
        "ResultTime": observed_at,
        "Pollutant": 8,
        "Verification": 4,
    }

    with pytest.raises(ValueError, match="Invalid Verification Code"):
        normalize_observation(
            raw_observation, {}, "https://example.com/observation.parquet"
        )


def test_checked_in_import_summary_matches_observations() -> None:
    path = Path("src") / "data" / "observations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    observations = document["observations"]
    import_summary = document["importSummary"]
    assert import_summary["imported"] == len(observations)
    assert (
        import_summary["attempted"]
        == import_summary["imported"]
        + import_summary["skipped"]
        + import_summary["failed"]
    )


def test_collect_observations_counts_failed_and_skipped(monkeypatch) -> None:
    def fake_fetch_latest_observation(parquet_url: str) -> dict:
        if parquet_url == "failed":
            raise TimeoutError("Timed out")
        raise importer.NoValidObservationsError("No valid observations found")

    monkeypatch.setattr(
        importer, "fetch_latest_observation", fake_fetch_latest_observation
    )
    result = importer.collect_observations(["failed", "skipped"])
    assert result["observations"] == []
    assert result["importSummary"] == {
        "attempted": 2,
        "imported": 0,
        "skipped": 1,
        "failed": 1,
    }
