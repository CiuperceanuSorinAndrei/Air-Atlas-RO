import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pyarrow
import pytest

import scripts.import_eea as importer
from scripts.import_eea import (
    NoValidObservationsError,
    group_series_urls,
    load_station_metadata_cache,
    normalize_observation,
    parse_series_url,
    select_latest_valid_observation,
    select_valid_history,
)


def test_discovery_retries_transient_timeout(monkeypatch) -> None:
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 30
        if attempts < 3:
            raise TimeoutError("EEA timed out")
        return BytesIO(
            b"ParquetFileUrl\nhttps://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet\n"
        )

    monkeypatch.setattr(
        importer,
        "read_response",
        lambda request, limit: fake_urlopen(request, 30).read(),
    )
    monkeypatch.setattr(importer.time, "sleep", lambda seconds: None)

    assert importer.fetch_parquet_urls("RO", ["NO2"]) == [
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"
    ]
    assert attempts == 3


def test_selects_latest_valid_observation_when_newest_is_invalid() -> None:
    table = pyarrow.table(
        {
            "End": [
                datetime(2026, 3, 28, 10, 0, 0),
                datetime(2026, 3, 28, 11, 0, 0),
            ],
            "Validity": [1, -1],
            "Value": [20, 99],
        }
    )
    selected_observation = select_latest_valid_observation(table)
    assert selected_observation["Value"] == 20


def test_valid_history_keeps_old_valid_rows() -> None:
    table = pyarrow.table(
        {
            "End": [
                datetime(2020, 1, 1, 10, 0, 0),
                datetime(2026, 3, 28, 11, 0, 0),
                datetime(2026, 8, 15, 9, 0, 0),
            ],
            "Validity": [1, 2, -1],
            "Value": [20, 21, 99],
        }
    )
    selected_history = select_valid_history(table)
    assert len(selected_history) == 2
    assert selected_history[0]["Value"] == 20
    assert selected_history[1]["Value"] == 21


def test_import_history_normalizes_rows_with_one_metadata_lookup(
    monkeypatch, tmp_path: Path
) -> None:
    raw_row = {
        "Value": 20,
        "AggType": "hour",
        "Start": datetime(2020, 1, 1, 9, 0),
        "End": datetime(2020, 1, 1, 10, 0),
        "ResultTime": datetime(2020, 1, 1, 10, 0),
        "Pollutant": 8,
        "Samplingpoint": "RO/SPO-RO0080A_00008_100",
        "Unit": "ug.m-3",
        "Validity": 1,
        "Verification": 1,
    }
    newer_row = {
        **raw_row,
        "Value": 21,
        "Start": datetime(2026, 3, 28, 10, 0),
        "End": datetime(2026, 3, 28, 11, 0),
        "ResultTime": datetime(2026, 3, 28, 11, 0),
        "Verification": 2,
    }
    monkeypatch.setattr(
        importer, "fetch_valid_history", lambda url: [raw_row, newer_row]
    )
    metadata_calls = []

    def fake_metadata(station_id, cache_path):
        metadata_calls.append(station_id)
        return {
            "stationId": station_id,
            "stationName": "DJ-3",
            "longitude": 23.7787,
            "latitude": 44.3268,
        }

    monkeypatch.setattr(importer, "get_station_metadata", fake_metadata)
    history = importer.import_history(
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
        tmp_path / "cache.json",
    )

    assert [row["value"] for row in history] == [20.0, 21.0]
    assert [row["status"] for row in history] == ["validated", "preliminary"]
    assert [row["stationId"] for row in history] == ["RO0080A", "RO0080A"]
    assert metadata_calls == ["RO0080A"]


def test_raises_when_no_valid_observations_exist() -> None:
    table = pyarrow.table(
        {
            "End": [
                datetime(2026, 3, 28, 10, 0, 0),
                datetime(2026, 3, 28, 11, 0, 0),
            ],
            "Validity": [-1, -99],
        }
    )
    with pytest.raises(NoValidObservationsError, match="No valid observations found"):
        select_latest_valid_observation(table)


def test_raises_when_latest_valid_timestamp_is_not_unique() -> None:
    latest_end = datetime(2026, 3, 28, 11, 0, 0)
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
    observed_from = datetime(2026, 3, 28, 10, 0, 0)
    observed_to = datetime(2026, 3, 28, 11, 0, 0)
    raw_observation = {
        "Value": 20,
        "AggType": "hour",
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
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
    )

    assert normalized_observation["status"] == expected_status


def test_normalize_observation_rejects_unknown_verification() -> None:
    observed_at = datetime(2026, 3, 28, 10, 0, 0)
    raw_observation = {
        "Value": 20,
        "AggType": "hour",
        "Start": observed_at,
        "End": observed_at,
        "ResultTime": observed_at,
        "Pollutant": 8,
        "Samplingpoint": "RO/SPO-RO0080A_00008_100",
        "Unit": "ug.m-3",
        "Validity": 1,
        "Verification": 4,
    }

    with pytest.raises(ValueError, match="Invalid Verification Code"):
        normalize_observation(
            raw_observation,
            {
                "stationId": "RO0080A",
                "stationName": "DJ-3",
                "latitude": 44.3,
                "longitude": 23.7,
            },
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
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
    failed_url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_101.parquet"
    skipped_url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet"

    def fake_import_observation(parquet_url: str) -> dict:
        if parquet_url == failed_url:
            raise TimeoutError("Timed out")
        raise importer.NoValidObservationsError("No valid observations found")

    monkeypatch.setattr(importer, "import_observation", fake_import_observation)
    result = importer.collect_observations([failed_url, skipped_url])
    assert result["observations"] == []
    assert result["importSummary"] == {
        "attempted": 2,
        "imported": 0,
        "skipped": 1,
        "failed": 1,
    }


def test_parse_series_url_returns_station_pollutant_and_sequence() -> None:
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet"
    station_id, pollutant_code, sequence = parse_series_url(url)
    assert station_id == "RO0008R"
    assert pollutant_code == "00008"
    assert sequence == 100
    assert type(sequence) is int


def test_parse_series_url_rejects_missing_sequence() -> None:
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008.parquet"
    with pytest.raises((ValueError, TypeError)):
        parse_series_url(url)


def test_parse_series_url_rejects_invalid_prefix() -> None:
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/INVALID-RO0008R_00008_100.parquet"
    with pytest.raises((ValueError, TypeError)):
        parse_series_url(url)


def test_group_series_urls_groups_by_station_and_pollutant() -> None:
    url_list = [
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_102.parquet",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00005_100.parquet",
    ]
    grouped_urls = group_series_urls(url_list)
    assert len(grouped_urls) == 2
    assert ("RO0008R", "00008") in grouped_urls
    assert ("RO0008R", "00005") in grouped_urls
    assert len(grouped_urls[("RO0008R", "00008")]) == 2
    assert (
        100,
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet",
    ) in grouped_urls[("RO0008R", "00008")]
    assert (
        102,
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_102.parquet",
    ) in grouped_urls[("RO0008R", "00008")]
    assert len(grouped_urls[("RO0008R", "00005")]) == 1
    assert (
        100,
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00005_100.parquet",
    ) in grouped_urls[("RO0008R", "00005")]


def test_group_series_urls_orders_higher_sequence_first() -> None:
    url_list = [
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_102.parquet",
    ]
    grouped_urls = group_series_urls(url_list)
    group_list = grouped_urls[("RO0008R", "00008")]
    assert group_list[0][0] == 102
    assert group_list[1][0] == 100


def test_collect_observations_falls_back_and_stops_after_success(
    monkeypatch,
) -> None:
    url_100 = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_100.parquet"
    url_101 = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_101.parquet"
    url_102 = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00008_102.parquet"
    attempted_urls = []

    def fake_import_observation(parquet_url: str) -> dict:
        attempted_urls.append(parquet_url)
        if parquet_url == url_102:
            raise NoValidObservationsError("No valid observations found")
        if parquet_url == url_101:
            return {"sourceUrl": parquet_url}
        raise AssertionError("Older series should not be attempted after success")

    monkeypatch.setattr(importer, "import_observation", fake_import_observation)

    result = importer.collect_observations([url_100, url_101, url_102])

    assert attempted_urls == [url_102, url_101]
    assert result["observations"] == [{"sourceUrl": url_101}]
    assert result["importSummary"] == {
        "attempted": 2,
        "imported": 1,
        "skipped": 1,
        "failed": 0,
    }


def test_load_station_metadata_cache_returns_empty_dict_when_file_is_missing(
    tmp_path,
) -> None:
    cache_path = tmp_path / "cache.json"
    metadata = load_station_metadata_cache(cache_path)
    assert metadata == {}


def test_load_station_metadata_cache_reads_existing_json(tmp_path) -> None:
    cache_path = tmp_path / "cache.json"
    expected_metadata = {
        "RO0008R": {
            "stationId": "RO0008R",
            "stationName": "DJ-3",
            "longitude": 23.7787,
            "latitude": 44.3268,
        }
    }
    json_metadata = json.dumps(expected_metadata)
    cache_path.write_text(json_metadata, encoding="utf-8")
    resulted_metadata = load_station_metadata_cache(cache_path)
    assert resulted_metadata == expected_metadata


def test_get_station_metadata_returns_cached_station_without_fetching(
    tmp_path, monkeypatch
) -> None:
    cache_path = tmp_path / "cache.json"
    expected_metadata = {
        "RO0008R": {
            "stationId": "RO0008R",
            "stationName": "DJ-3",
            "longitude": 23.7787,
            "latitude": 44.3268,
        }
    }
    cache_path.write_text(
        json.dumps(
            {
                key: {"metadata": value, "fetchedAt": datetime.now(UTC).isoformat()}
                for key, value in expected_metadata.items()
            }
        ),
        encoding="utf-8",
    )

    def fail_if_called(station_id: str) -> dict:
        raise AssertionError(f"ArcGIS should not be called for {station_id}")

    monkeypatch.setattr(importer, "fetch_station_metadata", fail_if_called)

    result = importer.get_station_metadata("RO0008R", cache_path)

    assert result == expected_metadata["RO0008R"]


def test_get_station_metadata_fetches_and_caches_missing_station(
    tmp_path, monkeypatch
) -> None:
    cache_path = tmp_path / "cache.json"
    expected_station = {
        "stationId": "RO0008R",
        "stationName": "DJ-3",
        "longitude": 23.7787,
        "latitude": 44.3268,
    }
    fetched_station_ids = []

    def fake_fetch_station_metadata(station_id: str) -> dict:
        fetched_station_ids.append(station_id)
        return expected_station

    monkeypatch.setattr(importer, "fetch_station_metadata", fake_fetch_station_metadata)

    result = importer.get_station_metadata("RO0008R", cache_path)

    assert result == expected_station
    assert fetched_station_ids == ["RO0008R"]
    assert cache_path.exists()
    persisted_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert persisted_cache["RO0008R"]["metadata"] == expected_station


def _mock_station_metadata_endpoints(
    monkeypatch,
    rows: list[dict],
    total_rows: int | None = None,
    features: list[dict] | None = None,
    arcgis_error: Exception | None = None,
) -> list:
    requests = []

    def fake_urlopen(request, timeout: int):
        assert timeout == 30
        requests.append(request)
        if "/MapServer/0/query" in request.full_url:
            if arcgis_error is not None:
                raise arcgis_error
            payload = {"type": "FeatureCollection", "features": features or []}
        elif "/AQViewer/init?" in request.full_url:
            payload = {
                "Request": {
                    "Page": 0,
                    "SortBy": None,
                    "SortAscending": True,
                    "RequestFilter": {},
                }
            }
        elif "/AQViewer/filter?" in request.full_url:
            payload = {
                "Preview": {
                    "TotalRows": len(rows) if total_rows is None else total_rows,
                    "Rows": rows,
                }
            }
        else:
            raise AssertionError(f"Unexpected metadata URL: {request.full_url}")
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(
        importer,
        "read_response",
        lambda request, limit: fake_urlopen(request, 30).read(),
    )
    return requests


def test_dataflow_fallback_returns_and_caches_one_station_from_two_pollutant_rows(
    tmp_path, monkeypatch
) -> None:
    station_id = "RO0240A"
    row = {
        "Country": "Romania",
        "AirQualityStationEoICode": station_id,
        "AQStationName": "B-19",
        "Longitude": 26.07075,
        "Latitude": 44.42268,
    }
    requests = _mock_station_metadata_endpoints(
        monkeypatch,
        [dict(row, AirPollutant="PM10"), dict(row, AirPollutant="PM2.5")],
    )
    cache_path = tmp_path / "station_metadata.json"
    expected = {
        "stationId": station_id,
        "stationName": "B-19",
        "longitude": 26.07075,
        "latitude": 44.42268,
    }

    assert importer.get_station_metadata(station_id, cache_path) == expected
    assert (
        json.loads(cache_path.read_text(encoding="utf-8"))[station_id]["metadata"]
        == expected
    )
    assert len(requests) == 3
    assert requests[2].get_method() == "POST"
    body = json.loads(requests[2].data)
    assert body["RequestFilter"]["AirQualityStationEoICode"] == {
        "FieldName": "AirQualityStationEoICode",
        "Values": [station_id],
    }
    assert importer.get_station_metadata(station_id, cache_path) == expected
    assert len(requests) == 3


def test_arcgis_station_remains_primary_when_it_has_one_feature(
    tmp_path, monkeypatch
) -> None:
    station_id = "RO0080A"
    feature = {
        "properties": {"AirQualityStationEoICode": station_id, "AQStationName": "DJ-3"},
        "geometry": {"type": "Point", "coordinates": [23.7787, 44.3268]},
    }
    requests = _mock_station_metadata_endpoints(monkeypatch, [], features=[feature])

    result = importer.get_station_metadata(station_id, tmp_path / "cache.json")

    assert result == {
        "stationId": station_id,
        "stationName": "DJ-3",
        "longitude": 23.7787,
        "latitude": 44.3268,
    }
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("case", "error_type", "message"),
    [
        ("conflict", ValueError, "Conflicting Dataflow D station metadata"),
        ("incomplete", ValueError, "Incomplete Dataflow D results"),
        ("empty", ValueError, "No Dataflow D rows"),
        ("invalid_total", TypeError, "missing TotalRows integer"),
        ("wrong_id", ValueError, "station ID does not match"),
        ("wrong_country", ValueError, "not from Romania"),
        ("boolean_coordinate", TypeError, "expected a number"),
    ],
)
def test_dataflow_fallback_rejects_untrusted_results_without_caching(
    tmp_path, monkeypatch, case, error_type, message
) -> None:
    station_id = "RO0240A"
    row = {
        "Country": "Romania",
        "AirQualityStationEoICode": station_id,
        "AQStationName": "B-19",
        "Longitude": 26.07075,
        "Latitude": 44.42268,
    }
    rows = [row]
    total_rows = None
    if case == "conflict":
        rows = [row, dict(row, Longitude=26.08)]
    elif case == "incomplete":
        total_rows = 2
    elif case == "empty":
        rows = []
    elif case == "invalid_total":
        total_rows = True
    elif case == "wrong_id":
        rows = [dict(row, AirQualityStationEoICode="RO9999A")]
    elif case == "wrong_country":
        rows = [dict(row, Country="Hungary")]
    elif case == "boolean_coordinate":
        rows = [dict(row, Longitude=True)]
    _mock_station_metadata_endpoints(monkeypatch, rows, total_rows=total_rows)
    cache_path = tmp_path / "cache.json"

    with pytest.raises(error_type, match=message):
        importer.get_station_metadata(station_id, cache_path)

    assert not cache_path.exists()


@pytest.mark.parametrize(
    "arcgis_error",
    [
        HTTPError("https://example.com", 500, "Internal Server Error", None, None),
        URLError("ArcGIS unavailable"),
        TimeoutError("ArcGIS timed out"),
    ],
)
def test_arcgis_temporary_failure_uses_dataflow_and_caches(
    tmp_path, monkeypatch, arcgis_error
) -> None:
    station_id = "RO0240A"
    row = {
        "Country": "Romania",
        "AirQualityStationEoICode": station_id,
        "AQStationName": "B-19",
        "Longitude": 26.07075,
        "Latitude": 44.42268,
    }
    requests = _mock_station_metadata_endpoints(
        monkeypatch, [row], arcgis_error=arcgis_error
    )
    cache_path = tmp_path / "cache.json"

    metadata = importer.get_station_metadata(station_id, cache_path)

    assert metadata["stationId"] == station_id
    assert len(requests) == 3
    assert (
        json.loads(cache_path.read_text(encoding="utf-8"))[station_id]["metadata"]
        == metadata
    )


def test_arcgis_client_error_does_not_use_dataflow_or_write_cache(
    tmp_path, monkeypatch
) -> None:
    error = HTTPError("https://example.com", 404, "Not Found", None, None)
    requests = _mock_station_metadata_endpoints(monkeypatch, [], arcgis_error=error)
    cache_path = tmp_path / "cache.json"

    with pytest.raises(HTTPError) as caught:
        importer.get_station_metadata("RO0240A", cache_path)

    assert caught.value.code == 404
    assert len(requests) == 1
    assert not cache_path.exists()


def test_both_metadata_services_failing_does_not_write_cache(
    tmp_path, monkeypatch
) -> None:
    requests = []

    def fail_both(request, timeout: int):
        requests.append(request.full_url)
        raise HTTPError(request.full_url, 503, "Unavailable", None, None)

    monkeypatch.setattr(
        importer, "read_response", lambda request, limit: fail_both(request, 30)
    )
    cache_path = tmp_path / "cache.json"

    with pytest.raises(HTTPError) as error:
        importer.get_station_metadata("RO0240A", cache_path)

    assert error.value.code == 503
    assert len(requests) == 2
    assert not cache_path.exists()


def test_multiple_arcgis_features_do_not_use_dataflow_or_write_cache(
    tmp_path, monkeypatch
) -> None:
    station_id = "RO0080A"
    feature = {
        "properties": {"AirQualityStationEoICode": station_id, "AQStationName": "DJ-3"},
        "geometry": {"type": "Point", "coordinates": [23.7787, 44.3268]},
    }
    requests = _mock_station_metadata_endpoints(
        monkeypatch, [], features=[feature, feature]
    )
    cache_path = tmp_path / "cache.json"

    with pytest.raises(ValueError, match="Expected one metadata feature"):
        importer.get_station_metadata(station_id, cache_path)

    assert len(requests) == 1
    assert not cache_path.exists()


def _snapshot(*pairs: tuple[str, str]) -> dict:
    observations = []
    for station_id, pollutant in pairs:
        code = "00008" if pollutant == "NO2" else "00005"
        observations.append(
            {
                "stationId": station_id,
                "pollutant": pollutant,
                "stationName": "Station",
                "source": "EEA",
                "samplingPointId": f"RO/SPO-{station_id}_{code}_100",
                "sourceUrl": f"https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-{station_id}_{code}_100.parquet",
                "value": 20.0,
                "unit": "ug.m-3",
                "latitude": 44.3,
                "longitude": 23.7,
                "observedFrom": "2026-03-28T10:00:00+01:00",
                "observedTo": "2026-03-28T11:00:00+01:00",
                "reportedAt": "2026-03-28T11:00:00+01:00",
                "ingestedAt": "2026-03-28T12:00:00Z",
                "validity": 1,
                "verification": 1,
                "status": "validated",
            }
        )
    return {
        "observations": observations,
        "importSummary": {
            "attempted": len(observations),
            "imported": len(observations),
            "skipped": 0,
            "failed": 0,
        },
    }


@pytest.mark.parametrize(
    "candidate",
    [
        _snapshot(),
        _snapshot(("RO0080A", "NO2")),
    ],
)
def test_incomplete_import_preserves_existing_snapshot(tmp_path, candidate) -> None:
    path = tmp_path / "observations.json"
    previous = _snapshot(("RO0080A", "NO2"), ("RO0240A", "PM10"))
    path.write_text(json.dumps(previous), encoding="utf-8")

    with pytest.raises(ValueError, match="keeping the previous snapshot"):
        importer.write_observation_snapshot(candidate, path)

    assert json.loads(path.read_text(encoding="utf-8")) == previous
    assert list(tmp_path.glob("*.tmp")) == []


def test_complete_import_replaces_snapshot(tmp_path) -> None:
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(_snapshot(("RO0080A", "NO2"))), encoding="utf-8")
    candidate = _snapshot(("RO0080A", "NO2"), ("RO0240A", "PM10"))

    importer.write_observation_snapshot(candidate, path)

    assert json.loads(path.read_text(encoding="utf-8")) == candidate
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_preserves_snapshot_and_removes_temp(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "observations.json"
    previous = _snapshot(("RO0080A", "NO2"))
    path.write_text(json.dumps(previous), encoding="utf-8")
    candidate = _snapshot(("RO0080A", "NO2"), ("RO0240A", "PM10"))

    def fail_replace(self: Path, target: Path) -> None:
        assert target == path
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        importer.write_observation_snapshot(candidate, path)

    assert json.loads(path.read_text(encoding="utf-8")) == previous
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("value", float("nan")),
        ("value", float("inf")),
        ("value", True),
        ("latitude", True),
        ("longitude", float("nan")),
        ("latitude", 91),
        ("unit", None),
        ("unit", "mg.m-3"),
        ("validity", True),
        ("verification", True),
        ("status", "modelled"),
        ("stationId", "RO9999A"),
        ("sourceUrl", "file:///tmp/secret.parquet"),
        ("samplingPointId", None),
        ("observedTo", "2030-01-01T00:00:00Z"),
        ("observedFrom", "2026-03-28T12:00:00Z"),
        ("reportedAt", "2027-01-01T00:00:00Z"),
        ("ingestedAt", "2026-03-28T12:00:00"),
        ("aggregationType", "year"),
        ("dataCapture", 101),
    ],
)
def test_invalid_snapshot_preserves_bytes(tmp_path, key, value):
    path = tmp_path / "observations.json"
    document = _snapshot(("RO0080A", "NO2"))
    importer.write_observation_snapshot(document, path)
    before = path.read_bytes()
    document["observations"][0][key] = value
    with pytest.raises((ValueError, TypeError)):
        importer.write_observation_snapshot(document, path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))


def test_daily_and_legacy_daily_are_valid():
    document = _snapshot(("RO0080A", "PM10"))
    row = document["observations"][0]
    row["observedFrom"] = "2026-03-27T11:00:00+01:00"
    importer.validate_document(document)
    row["aggregationType"] = "day"
    importer.validate_document(document)
    row["aggregationType"] = "hour"
    with pytest.raises((ValueError, TypeError)):
        importer.validate_document(document)


def test_regression_rejected_but_same_interval_correction_accepted(tmp_path):
    path = tmp_path / "observations.json"
    document = _snapshot(("RO0080A", "NO2"))
    importer.write_observation_snapshot(document, path)
    before = path.read_bytes()
    regressed = _snapshot(("RO0080A", "NO2"))
    regressed["observations"][0]["observedFrom"] = "2026-03-28T09:00:00+01:00"
    regressed["observations"][0]["observedTo"] = "2026-03-28T10:00:00+01:00"
    with pytest.raises(ValueError, match="regressed"):
        importer.write_observation_snapshot(regressed, path)
    assert path.read_bytes() == before
    document["observations"][0]["value"] = 21
    importer.write_observation_snapshot(document, path)
    assert json.loads(path.read_text())["observations"][0]["value"] == 21


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/SPO-RO0080A_00008_100.parquet",
        "https://localhost/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
        "http://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet?x=1",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/../RO/SPO-RO0080A_00008_100.parquet",
    ],
)
def test_unsafe_source_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr(
        importer, "read_response", lambda *args: pytest.fail("network called")
    )
    with pytest.raises((ValueError, TypeError)):
        importer.fetch_parquet_table(url)


def test_bounded_http_response(monkeypatch):
    class Response(BytesIO):
        status = 200
        headers = None

        def __init__(self, data):
            super().__init__(data)
            self.headers = {}

        def geturl(self):
            return "https://discomap.eea.europa.eu/"

    class Opener:
        def open(self, request, timeout):
            return Response(b"x" * 11)

    monkeypatch.setattr(importer.urllib.request, "build_opener", lambda *args: Opener())
    request = importer.urllib.request.Request("https://discomap.eea.europa.eu/")
    with pytest.raises(ValueError, match="download limit"):
        importer.read_response(request, 10)


def test_redirect_cannot_leave_provider():
    request = importer.urllib.request.Request("https://discomap.eea.europa.eu/")
    with pytest.raises((ValueError, TypeError)):
        importer.SafeRedirectHandler().redirect_request(
            request, None, 302, "", {}, "http://127.0.0.1/"
        )


@pytest.mark.parametrize("age", [2, -1])
def test_cache_expired_or_future_is_refetched(monkeypatch, tmp_path, age):
    from datetime import timedelta

    path = tmp_path / "metadata.json"
    metadata = {
        "stationId": "RO0080A",
        "stationName": "Craiova",
        "latitude": 44.3,
        "longitude": 23.7,
    }
    path.write_text(
        json.dumps(
            {
                "RO0080A": {
                    "metadata": metadata,
                    "fetchedAt": (datetime.now(UTC) - timedelta(days=age)).isoformat(),
                }
            }
        )
    )
    calls = []

    def fetch(station):
        calls.append(station)
        return metadata

    monkeypatch.setattr(importer, "fetch_station_metadata", fetch)
    assert importer.get_station_metadata("RO0080A", path) == metadata
    assert calls == ["RO0080A"]


def test_cache_io_failure_does_not_drop_valid_metadata(monkeypatch, tmp_path):
    metadata = {
        "stationId": "RO0080A",
        "stationName": "Craiova",
        "latitude": 44.3,
        "longitude": 23.7,
    }
    monkeypatch.setattr(importer, "fetch_station_metadata", lambda station: metadata)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(importer, "atomic_write_json", fail)
    assert (
        importer.get_station_metadata("RO0080A", tmp_path / "metadata.json") == metadata
    )


def test_history_identity_mismatch_precedes_metadata_fetch(monkeypatch):
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"
    rows = [
        {"Samplingpoint": "RO/SPO-RO0080A_00008_100", "Pollutant": 8},
        {"Samplingpoint": "RO/SPO-RO9999A_00008_100", "Pollutant": 8},
    ]
    monkeypatch.setattr(importer, "fetch_valid_history", lambda _: rows)
    monkeypatch.setattr(
        importer, "get_station_metadata", lambda *args: pytest.fail("metadata called")
    )
    with pytest.raises(ValueError, match="sampling point"):
        importer.import_history(url)


def test_conflicting_physical_station_metadata_rejected():
    document = _snapshot(("RO0080A", "NO2"), ("RO0080A", "PM10"))
    document["observations"][1]["latitude"] += 1
    with pytest.raises(ValueError, match="Conflicting metadata"):
        importer.validate_document(document)


@pytest.mark.parametrize("rows,columns", [(200001, 1), (1, 33)])
def test_parquet_limits_precede_decoding(monkeypatch, rows, columns):
    class Metadata:
        num_rows = rows
        num_columns = columns
        num_row_groups = 0

    class Parquet:
        metadata = Metadata()

        def read(self):
            pytest.fail("oversized Parquet decoded")

    monkeypatch.setattr(importer, "read_response", lambda *args: b"file")
    monkeypatch.setattr(importer.pq, "ParquetFile", lambda *args, **kwargs: Parquet())
    with pytest.raises(ValueError, match="limit exceeded"):
        importer.fetch_parquet_table(
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"
        )


def test_transient_blob_503_retries_without_changing_identity(monkeypatch):
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"
    table = pyarrow.table({"Validity": [1], "Value": [20]})
    buffer = pyarrow.BufferOutputStream()
    importer.pq.write_table(table, buffer)
    data = buffer.getvalue().to_pybytes()
    calls = []

    def response(request, limit):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(url, 503, "temporary provider failure", {}, None)
        return data

    monkeypatch.setattr(importer, "read_response", response)
    monkeypatch.setattr(importer.time, "sleep", lambda delay: None)
    assert importer.fetch_parquet_table(url).to_pylist() == table.to_pylist()
    assert calls == [url, url]


def test_blob_403_remains_visible_without_retry(monkeypatch):
    calls = []

    def response(request, limit):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "denied", {}, None)

    monkeypatch.setattr(importer, "read_response", response)
    monkeypatch.setattr(importer.time, "sleep", lambda delay: None)
    with pytest.raises(HTTPError):
        importer.fetch_parquet_table(
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"
        )
    assert len(calls) == 1


def test_fallback_init_uses_viewer_client_headers(monkeypatch):
    requests = _mock_station_metadata_endpoints(
        monkeypatch,
        [
            {
                "AirQualityStationEoICode": "RO0260A",
                "Country": "Romania",
                "AQStationName": "Test",
                "Longitude": 23.7,
                "Latitude": 44.3,
            }
        ],
    )
    importer.fetch_station_metadata("RO0260A")
    assert requests[1].get_header("User-agent") == "Mozilla/5.0"
    assert (
        requests[1]
        .get_header("Referer")
        .startswith("https://discomap.eea.europa.eu/App/AQViewer/index.html")
    )


def test_collect_history_preserves_series_intervals_and_quality(monkeypatch, tmp_path):
    first = _snapshot(("RO0080A", "NO2"))["observations"][0]
    first["aggregationType"] = "hour"
    second = {
        **first,
        "samplingPointId": "RO/SPO-RO0080A_00008_101",
        "sourceUrl": first["sourceUrl"].replace("_100", "_101"),
    }
    older = {
        **first,
        "observedFrom": "2020-01-01T09:00:00+01:00",
        "observedTo": "2020-01-01T10:00:00+01:00",
    }
    duplicate = {**first, "ingestedAt": "2026-03-28T13:00:00Z"}
    calls = []

    def fetch(url, cache_path):
        calls.append((url, cache_path))
        return [first, duplicate, older] if url == first["sourceUrl"] else [second]

    monkeypatch.setattr(importer, "import_history", fetch)
    cache_path = tmp_path / "metadata.json"
    result = importer.collect_history(
        [first["sourceUrl"], second["sourceUrl"], first["sourceUrl"]], cache_path
    )
    assert result["observations"] == [first, older, second]
    assert result["importSummary"] == {
        "attempted": 2,
        "imported": 2,
        "skipped": 0,
        "failed": 0,
        "duplicateCount": 1,
        "observationCount": 3,
    }
    assert calls == [
        (first["sourceUrl"], cache_path),
        (second["sourceUrl"], cache_path),
    ]


@pytest.mark.parametrize("changed", ["value", "verification", "reportedAt"])
def test_collect_history_discards_entire_conflicting_series(monkeypatch, changed):
    first = _snapshot(("RO0080A", "NO2"))["observations"][0]
    first["aggregationType"] = "hour"
    other = _snapshot(("RO0009R", "PM10"))["observations"][0]
    other["aggregationType"] = "hour"
    conflict = {**first}
    if changed == "value":
        conflict[changed] = 99.0
    elif changed == "verification":
        conflict.update(verification=2, status="preliminary")
    else:
        conflict[changed] = "2026-03-28T11:30:00+01:00"
    monkeypatch.setattr(
        importer,
        "import_history",
        lambda url, cache: [first, conflict] if url == first["sourceUrl"] else [other],
    )
    result = importer.collect_history([first["sourceUrl"], other["sourceUrl"]])
    assert result["observations"] == [other]
    assert result["importSummary"] == {
        "attempted": 2,
        "imported": 1,
        "skipped": 0,
        "failed": 1,
        "duplicateCount": 0,
        "observationCount": 1,
    }


def test_collect_history_counts_empty_network_and_invalid_series(monkeypatch):
    row = _snapshot(("RO0080A", "NO2"))["observations"][0]
    url = row["sourceUrl"]
    calls = []

    def fetch(candidate, cache):
        calls.append(candidate)
        if candidate == url:
            raise importer.NoValidObservationsError("empty")
        raise URLError("unavailable")

    monkeypatch.setattr(importer, "import_history", fetch)
    result = importer.collect_history([url, url.replace("_100", "_101"), "invalid"])
    assert result["observations"] == []
    assert result["importSummary"] == {
        "attempted": 3,
        "imported": 0,
        "skipped": 1,
        "failed": 2,
        "duplicateCount": 0,
        "observationCount": 0,
    }
    assert len(calls) == 2
    assert importer.collect_history([])["importSummary"]["attempted"] == 0


def test_collect_history_rejects_wrong_source_and_empty_result(monkeypatch):
    row = _snapshot(("RO0080A", "NO2"))["observations"][0]
    row["aggregationType"] = "hour"
    url = row["sourceUrl"].replace("_100", "_101")
    monkeypatch.setattr(importer, "import_history", lambda *args: [row])
    assert importer.collect_history([url])["importSummary"]["failed"] == 1
    monkeypatch.setattr(importer, "import_history", lambda *args: [])
    assert importer.collect_history([url])["importSummary"]["skipped"] == 1
