import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import import_eea as importer

STATION = "RO0240A"
ROW = {
    "Country": "Romania",
    "AirQualityStationEoICode": STATION,
    "AQStationName": "B-19",
    "Longitude": 26.07075,
    "Latitude": 44.42268,
}


def preview(rows, page=0, pages=2, total=2):
    return {
        "Preview": {
            "Rows": rows,
            "CurrentPage": page,
            "NumPages": pages,
            "TotalRows": total,
        }
    }


def install(monkeypatch, pages, station=STATION):
    requests = []

    def read(request, limit):
        assert limit == 8 * 1024 * 1024
        if "/MapServer/" in request.full_url:
            payload = {"type": "FeatureCollection", "features": []}
        elif "/init?" in request.full_url:
            payload = {"Request": {"Page": 0, "RequestFilter": {}}}
        else:
            body = json.loads(request.data)
            assert body["RequestFilter"]["AirQualityStationEoICode"]["Values"] == [
                station
            ]
            requests.append(body["Page"])
            payload = pages[body["Page"]]
        return json.dumps(payload).encode()

    monkeypatch.setattr(importer, "read_response", read)
    return requests


def test_complete_pagination_is_cached_only_after_all_pages(tmp_path, monkeypatch):
    pages = [
        preview([dict(ROW, pollutant="NO2")]),
        preview([dict(ROW, pollutant="PM10")], page=1),
    ]
    requests = install(monkeypatch, pages)
    cache = tmp_path / "metadata.json"
    result = importer.get_station_metadata(STATION, cache)
    assert result["longitude"] == ROW["Longitude"]
    assert requests == [0, 1]
    assert json.loads(cache.read_text())[STATION]["metadata"] == result


@pytest.mark.parametrize(
    "case",
    [
        "wrong_page",
        "boolean_page",
        "changed_total",
        "changed_pages",
        "empty",
        "repeat",
        "excess",
        "short",
        "wrong_id",
        "conflict",
        "malformed_rows",
        "boolean_total",
        "missing_paging",
        "too_many_pages",
        "row_limit",
    ],
)
def test_inconsistent_pages_never_enter_metadata_cache(tmp_path, monkeypatch, case):
    pages = [preview([dict(ROW, tag=0)]), preview([dict(ROW, tag=1)], page=1)]
    second = pages[1]["Preview"]
    if case == "wrong_page":
        second["CurrentPage"] = 0
    elif case == "boolean_page":
        second["CurrentPage"] = True
    elif case == "changed_total":
        second["TotalRows"] = 3
    elif case == "changed_pages":
        second["NumPages"] = 3
    elif case == "empty":
        second["Rows"] = []
    elif case == "repeat":
        second["Rows"] = deepcopy(pages[0]["Preview"]["Rows"])
    elif case == "excess":
        second["Rows"] *= 2
    elif case == "short":
        for p in pages:
            p["Preview"]["TotalRows"] = 3
    elif case == "wrong_id":
        second["Rows"][0]["AirQualityStationEoICode"] = "RO9999A"
    elif case == "conflict":
        second["Rows"][0]["Longitude"] += 1
    elif case == "malformed_rows":
        second["Rows"] = None
    elif case == "boolean_total":
        second["TotalRows"] = True
    elif case == "missing_paging":
        del pages[0]["Preview"]["CurrentPage"]
    elif case == "too_many_pages":
        pages[0]["Preview"]["NumPages"] = 65
    elif case == "row_limit":
        pages[0]["Preview"]["TotalRows"] = 2001
    install(monkeypatch, pages)
    cache = tmp_path / "metadata.json"
    with pytest.raises((TypeError, ValueError)):
        importer.get_station_metadata(STATION, cache)
    assert not cache.exists()


def test_captured_34_rows_remain_conflicting_after_complete_read(tmp_path, monkeypatch):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/eea_metadata_ro0143_pages.json").read_text()
    )
    pages = fixture["pages"]
    assert [len(p["Preview"]["Rows"]) for p in pages] == [30, 4]
    requests = install(monkeypatch, pages, station="RO0143A")
    with pytest.raises(ValueError, match="Conflicting Dataflow D station metadata"):
        importer.get_station_metadata("RO0143A", tmp_path / "metadata.json")
    assert requests == [0, 1]
    assert not (tmp_path / "metadata.json").exists()


def test_refresh_can_stop_after_first_blocking_failure(monkeypatch):
    urls = [
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0240A_00008_100.parquet",
        "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0241A_00008_100.parquet",
    ]
    calls = []

    def fail(url):
        calls.append(url)
        raise ValueError("Conflicting metadata")

    monkeypatch.setattr(importer, "import_observation", fail)
    with pytest.raises(ValueError, match="Snapshot import blocked"):
        importer.collect_observations(urls, fail_fast=True)
    assert calls == urls[:1]
    calls.clear()
    result = importer.collect_observations(urls)
    assert calls == urls
    assert result["importSummary"]["failed"] == 2


def test_fast_failure_preserves_sequence_fallback_for_empty_series(monkeypatch):
    row = json.loads((Path(__file__).parent / "fixtures/eea_dj1_no2.json").read_text())[
        "eeaObservation"
    ]
    old = row["sourceUrl"]
    new = old.replace("_100.parquet", "_101.parquet")

    def import_row(url):
        if url == new:
            raise importer.NoValidObservationsError("Empty")
        return row

    monkeypatch.setattr(importer, "import_observation", import_row)
    result = importer.collect_observations([old, new], fail_fast=True)
    assert result["observations"] == [row]
    assert result["importSummary"]["skipped"] == 1


def test_cli_keeps_previous_snapshot_on_metadata_failure(tmp_path, monkeypatch):
    path = tmp_path / "src/data/observations.json"
    path.parent.mkdir(parents=True)
    previous = b'{"previousSnapshot":true}'
    path.write_bytes(previous)
    monkeypatch.setattr(importer, "__file__", str(tmp_path / "scripts/import_eea.py"))
    monkeypatch.setattr(importer.sys, "argv", ["import_eea"])
    monkeypatch.setattr(
        importer,
        "fetch_parquet_urls",
        lambda *args: [
            "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0240A_00008_100.parquet"
        ],
    )

    def fail(url):
        raise ValueError("Metadata unavailable")

    monkeypatch.setattr(importer, "import_observation", fail)
    with pytest.raises(ValueError, match="Snapshot import blocked"):
        importer.main()
    assert path.read_bytes() == previous
