import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import eea_catalog as catalog
from scripts import import_eea as importer

BASE = f"https://{importer.PARQUET_HOST}/airquality-p/RO/"
URL = BASE + "SPO-RO0008R_00008_100.parquet"
OLDER = BASE + "SPO-RO0008R_00008_99.parquet"
NEW = BASE + "SPO-RO0008R_00008_101.parquet"
OTHER = BASE + "SPO-RO0008R_00005_100.parquet"


@pytest.fixture
def registries():
    station = {
        "stationId": "RO0008R",
        "stationName": "SYNTHETIC TEST STATION",
        "latitude": 47.324686,
        "longitude": 25.134839,
        "namespace": "RO.ANPM.AQ",
        "localId": "STA-RO0008R",
        "metadataSourceUrl": "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envtest/report.xml",
        "metadataEnvelopeUrl": "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envtest/",
        "metadataReportingYear": 2025,
        "metadataSourceSha256": "a" * 64,
        "metadataDelivery": {
            "headerId": "SYNTHETIC-NOT-AN-APPROVED-DELIVERY",
            "reportingPeriodStart": "2025-01-01T00:00:00+01:00",
            "reportingPeriodEnd": "2026-01-01T00:00:00+01:00",
        },
    }
    cat = {"schemaVersion": 1, "catalogVersion": 1, "stations": [station]}
    inv = {
        "schemaVersion": 1,
        "inventoryVersion": 1,
        "catalogVersion": 1,
        "channels": [
            {
                "stationId": "RO0008R",
                "pollutant": "NO2",
                "streams": [
                    {"samplingPointId": "RO/SPO-RO0008R_00008_100", "sourceUrl": URL},
                ],
            }
        ],
    }
    return cat, inv


def raw_observation():
    return {
        "Value": 20,
        "AggType": "hour",
        "Pollutant": 8,
        "Samplingpoint": "RO/SPO-RO0008R_00008_100",
        "Unit": "ug.m-3",
        "Validity": 1,
        "Verification": 1,
        "Start": datetime(2026, 10, 28, 10, tzinfo=UTC).replace(tzinfo=None),
        "End": datetime(2026, 10, 28, 11, tzinfo=UTC).replace(tzinfo=None),
        "ResultTime": datetime(2026, 10, 28, 11, tzinfo=UTC).replace(tzinfo=None),
    }


def test_exact_native_streams_and_stable_versions(registries):
    cat, inv = registries
    original = deepcopy(registries)
    report = catalog.reconcile_discovery([NEW, OTHER, URL], cat, inv)
    assert report["matchedStreams"] == [URL]
    assert report["missingChannels"] == []
    assert report["missingStreams"] == []
    assert report["unregisteredChannels"] == [["RO0008R", "PM10"]]
    assert report["unregisteredStreams"] == sorted([NEW, OTHER])
    missing = catalog.reconcile_discovery([NEW], cat, inv)
    assert missing["missingChannels"] == [["RO0008R", "NO2"]]
    assert missing["missingStreams"] == [URL]
    assert missing["unregisteredChannels"] == []
    assert catalog.reconcile_discovery([], cat, inv)["missingChannels"] == [
        ["RO0008R", "NO2"]
    ]
    assert catalog.reconcile_discovery([URL], cat, inv)["inventoryVersion"] == 1
    assert registries == original


def test_missing_sequence_is_visible_even_when_channel_present(registries):
    cat, inv = registries
    inv["channels"][0]["streams"].append(
        {"samplingPointId": "RO/SPO-RO0008R_00008_99", "sourceUrl": OLDER}
    )
    report = catalog.reconcile_discovery([URL], cat, inv)
    assert report["missingChannels"] == []
    assert report["missingStreams"] == [OLDER]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stationId", []),
        ("stationId", "XX0008R"),
        ("stationName", ""),
        ("latitude", True),
        ("latitude", float("nan")),
        ("longitude", float("inf")),
        ("latitude", 91),
        ("longitude", -181),
        ("namespace", "BG.AQ"),
        ("localId", "STA-RO9999R"),
        ("metadataSourceUrl", "https://evil.test/report.xml"),
        (
            "metadataSourceUrl",
            "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envtest/report.xml?q=x",
        ),
        (
            "metadataSourceUrl",
            "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envtest/no.txt",
        ),
        ("metadataEnvelopeUrl", "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envother/"),
        ("metadataReportingYear", True),
        ("metadataReportingYear", 2012),
        ("metadataReportingYear", 9999),
        ("metadataSourceSha256", "bad"),
        ("metadataSourceSha256", "A" * 64),
        ("metadataDelivery", {}),
    ],
)
def test_catalog_rejects_invalid_identity_geometry_and_provenance(
    registries, field, value
):
    cat, _ = registries
    cat["stations"][0][field] = value
    with pytest.raises((ValueError, TypeError)):
        catalog.validate_station_catalog(cat)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "empty",
        "boolean_schema",
        "boolean_version",
        "unknown_field",
        "period",
        "naive_period",
        "microsecond_period",
        "header",
    ],
)
def test_catalog_shape_and_delivery_contract(registries, case):
    cat, _ = registries
    station = cat["stations"][0]
    if case == "duplicate":
        cat["stations"].append(deepcopy(station))
    elif case == "empty":
        cat["stations"] = []
    elif case == "boolean_schema":
        cat["schemaVersion"] = True
    elif case == "boolean_version":
        cat["catalogVersion"] = True
    elif case == "unknown_field":
        station["rnmcMappingByName"] = "DJ-1"
    elif case == "period":
        station["metadataDelivery"]["reportingPeriodStart"] = "2024-01-01T00:00:00Z"
    elif case == "naive_period":
        station["metadataDelivery"]["reportingPeriodStart"] = "2025-01-01T00:00:00"
    elif case == "microsecond_period":
        station["metadataDelivery"]["reportingPeriodStart"] = (
            "2025-01-01T00:00:00.000001Z"
        )
    elif case == "header":
        station["metadataDelivery"]["headerId"] = ""
    with pytest.raises((ValueError, TypeError)):
        catalog.validate_station_catalog(cat)


@pytest.mark.parametrize(
    "case",
    [
        "station",
        "pollutant",
        "duplicate_channel",
        "duplicate_sequence",
        "sampling_id",
        "wrong_pollutant",
        "no_stream",
        "catalog_version",
        "schema",
        "bad_url",
    ],
)
def test_inventory_rejects_unreviewed_or_inconsistent_identity(registries, case):
    cat, inv = registries
    channel = inv["channels"][0]
    stream = channel["streams"][0]
    if case == "station":
        channel["stationId"] = "RO9999A"
    elif case == "pollutant":
        channel["pollutant"] = []
    elif case == "duplicate_channel":
        inv["channels"].append(deepcopy(channel))
    elif case == "duplicate_sequence":
        channel["streams"].append(deepcopy(stream))
    elif case == "sampling_id":
        stream["samplingPointId"] = "RO/SPO-RO0008R_00008_101"
    elif case == "wrong_pollutant":
        channel["pollutant"] = "PM10"
    elif case == "no_stream":
        channel["streams"] = []
    elif case == "catalog_version":
        inv["catalogVersion"] = 2
    elif case == "schema":
        inv["schemaVersion"] = True
    elif case == "bad_url":
        stream["sourceUrl"] = "https://evil.test/file.parquet"
    with pytest.raises((ValueError, TypeError)):
        catalog.validate_channel_inventory(inv, cat)


def test_catalog_has_no_seven_day_expiry_and_arcgis_is_review_only(registries):
    cat, _ = registries
    cat["stations"][0]["metadataReportingYear"] = 2013
    cat["stations"][0]["metadataDelivery"].update(
        reportingPeriodStart="2013-01-01T00:00:00Z",
        reportingPeriodEnd="2014-01-01T00:00:00Z",
    )
    assert "RO0008R" in catalog.validate_station_catalog(cat)
    station = cat["stations"][0]
    station["arcgis"] = {
        key: station[key]
        for key in ("stationId", "stationName", "latitude", "longitude")
    }
    assert catalog.cross_check_findings(cat) == []
    station["arcgis"]["latitude"] += 0.00005
    assert catalog.cross_check_findings(cat) == []
    station["arcgis"]["latitude"] += 0.00005
    (finding,) = catalog.cross_check_findings(cat)
    assert 11 < finding["distanceMeters"] < 12
    assert station["latitude"] == 47.324686


@pytest.mark.parametrize("fail_fast", [False, True])
@pytest.mark.parametrize(
    "error", [TimeoutError("timed out"), ValueError("identity mismatch")]
)
def test_hard_error_never_falls_back_to_older_sequence(monkeypatch, fail_fast, error):
    calls = []

    def fail(url):
        calls.append(url)
        raise error

    monkeypatch.setattr(importer, "import_observation", fail)
    if fail_fast:
        with pytest.raises(ValueError, match="Snapshot import blocked"):
            importer.collect_observations([URL, NEW], fail_fast=True)
    else:
        result = importer.collect_observations([URL, NEW])
        assert result["importSummary"] == {
            "attempted": 1,
            "failed": 1,
            "skipped": 0,
            "imported": 0,
        }
    assert calls == [NEW]


def test_catalog_collection_has_no_metadata_io_and_excludes_unknown_streams(
    registries, monkeypatch
):
    cat, inv = registries
    calls = []

    def latest(url):
        calls.append(url)
        return raw_observation()

    def forbidden(*args, **kwargs):
        pytest.fail("Catalog collection attempted metadata or cache I/O")

    monkeypatch.setattr(importer, "fetch_latest_observation", latest)
    monkeypatch.setattr(importer, "get_station_metadata", forbidden)
    monkeypatch.setattr(importer, "fetch_station_metadata", forbidden)
    monkeypatch.setattr(importer, "load_station_metadata_cache", forbidden)
    result = catalog.collect_catalog_observations([NEW, URL], cat, inv)
    (row,) = result["collection"]["observations"]
    assert calls == [URL]
    assert row["value"] == 20
    assert row["stationName"] == "SYNTHETIC TEST STATION"
    assert row["latitude"] == cat["stations"][0]["latitude"]
    assert "metadataDelivery" not in row
    assert result["discovery"]["unregisteredStreams"] == [NEW]
    importer.validate_observation(row)


def test_catalog_empty_eligible_sequence_allows_lower_priority(registries, monkeypatch):
    cat, inv = registries
    inv["channels"][0]["streams"].append(
        {"samplingPointId": "RO/SPO-RO0008R_00008_101", "sourceUrl": NEW}
    )
    calls = []

    def latest(url):
        calls.append(url)
        if url == NEW:
            raise importer.NoValidObservationsError("No eligible observation")
        return raw_observation()

    monkeypatch.setattr(importer, "fetch_latest_observation", latest)
    assert (
        len(
            catalog.collect_catalog_observations([URL, NEW], cat, inv)["collection"][
                "observations"
            ]
        )
        == 1
    )
    assert calls == [NEW, URL]


def test_catalog_rejects_missing_station_or_geometry_before_network(
    registries, monkeypatch
):
    cat, inv = registries

    def forbidden(*args, **kwargs):
        pytest.fail("Network called before registry validation")

    monkeypatch.setattr(importer, "fetch_latest_observation", forbidden)
    with pytest.raises(ValueError, match="absent"):
        importer.collect_observations(
            [BASE + "SPO-RO9999A_00008_100.parquet"], station_catalog=cat
        )
    with pytest.raises(ValueError, match="complete reviewed"):
        catalog.collect_catalog_observations([NEW], cat, inv)
    station = cat["stations"][0]
    station["arcgis"] = {
        key: station[key]
        for key in ("stationId", "stationName", "latitude", "longitude")
    }
    station["arcgis"]["latitude"] += 0.001
    with pytest.raises(ValueError, match="complete reviewed"):
        catalog.collect_catalog_observations([URL], cat, inv)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"Wrong\n",
        b"ParquetFileUrl\n",
        b"x" * (2 * 1024 * 1024 + 1),
        b"\xff",
        f"ParquetFileUrl\n{URL}\n{URL}\n".encode(),
        b"ParquetFileUrl\nhttps://evil.test/no.parquet",
    ],
)
def test_discovery_parser_rejects_malformed_oversized_or_duplicate_payload(body):
    with pytest.raises((ValueError, TypeError)):
        importer.parse_parquet_urls_payload(body)


def test_discovery_parser_bom_and_reconciliation_bounds(registries):
    assert importer.parse_parquet_urls_payload(
        f"\ufeffParquetFileUrl\r\n{URL}\r\n".encode()
    ) == [URL]
    cat, inv = registries
    for urls in ([URL, URL], [URL] * (catalog.MAX_DISCOVERY_URLS + 1), "bad"):
        with pytest.raises(ValueError):
            catalog.reconcile_discovery(urls, cat, inv)


def test_bounded_read_and_duplicate_json_keys(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b"x" * 101)
    with pytest.raises(ValueError, match="byte limit"):
        catalog.read_bounded(path, 100)
    with pytest.raises(ValueError, match="Duplicate JSON"):
        json.loads(
            '{"schemaVersion":1,"schemaVersion":2}',
            object_pairs_hook=catalog.unique_object,
        )


def test_offline_cli_never_changes_inputs_or_snapshot(registries, tmp_path):
    cat, inv = registries
    cat_path, inv_path, urls_path = (
        tmp_path / name for name in ("catalog.json", "inventory.json", "discovery.txt")
    )
    cat_path.write_text(json.dumps(cat))
    inv_path.write_text(json.dumps(inv))
    snapshot = Path("src/data/observations.json")
    before = snapshot.read_bytes()
    command = [
        sys.executable,
        "-m",
        "scripts.eea_catalog",
        "--catalog",
        str(cat_path),
        "--inventory",
        str(inv_path),
    ]
    original = [cat_path.read_bytes(), inv_path.read_bytes()]
    valid = subprocess.run(command, capture_output=True, text=True)
    assert valid.returncode == 0, valid.stderr
    assert json.loads(valid.stdout)["channels"] == 1
    urls_path.write_text(f"ParquetFileUrl\n{URL}\n")
    complete = subprocess.run(
        [*command, "--discovery", str(urls_path)], capture_output=True, text=True
    )
    assert complete.returncode == 0, complete.stderr
    urls_path.write_text(f"ParquetFileUrl\n{NEW}\n")
    missing = subprocess.run(
        [*command, "--discovery", str(urls_path)], capture_output=True, text=True
    )
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["missingChannels"] == [["RO0008R", "NO2"]]
    urls_path.write_text("INVALID\n")
    invalid = subprocess.run(
        [*command, "--discovery", str(urls_path)], capture_output=True, text=True
    )
    assert invalid.returncode == 1
    assert invalid.stderr == "Invalid catalog, inventory or discovery input.\n"
    assert [cat_path.read_bytes(), inv_path.read_bytes()] == original
    assert snapshot.read_bytes() == before


def test_hard_failure_skips_older_sequence_but_continues_other_channel(monkeypatch):
    calls = []

    def latest(url):
        calls.append(url)
        if url == NEW:
            raise TimeoutError("Timed out")
        assert url == OTHER
        return {"checked": True}

    monkeypatch.setattr(importer, "import_observation", latest)
    result = importer.collect_observations([URL, NEW, OTHER])
    assert calls == [NEW, OTHER]
    assert result["observations"] == [{"checked": True}]
    assert result["importSummary"] == {
        "attempted": 2,
        "failed": 1,
        "skipped": 0,
        "imported": 1,
    }


@pytest.mark.parametrize(
    "urls", [[URL, URL], [URL, URL.replace("_100.parquet", "_0100.parquet")]]
)
def test_duplicate_series_identity_is_rejected_before_fetch(monkeypatch, urls):
    def forbidden(*args, **kwargs):
        pytest.fail("Duplicate input performed network I/O")

    monkeypatch.setattr(importer, "import_observation", forbidden)
    with pytest.raises(ValueError, match="Duplicate EEA series identity"):
        importer.collect_observations(urls)


def test_reporting_period_offsets_must_agree_without_inventing_provider_zone(
    registries,
):
    cat, _ = registries
    delivery = cat["stations"][0]["metadataDelivery"]
    delivery["reportingPeriodStart"] = "2025-01-01T00:00:00+14:00"
    delivery["reportingPeriodEnd"] = "2026-01-01T00:00:00-12:00"
    with pytest.raises(ValueError, match="offsets must agree"):
        catalog.validate_station_catalog(cat)


@pytest.mark.parametrize("mode", ["geometry", "new_stream"])
def test_cli_review_exit_for_geometry_and_unknown_stream_only(
    registries, tmp_path, mode
):
    cat, inv = registries
    station = cat["stations"][0]
    if mode == "geometry":
        station["arcgis"] = {
            key: station[key]
            for key in ("stationId", "stationName", "latitude", "longitude")
        }
        station["arcgis"]["longitude"] += 0.001
    cat_path, inv_path = tmp_path / "catalog.json", tmp_path / "inventory.json"
    cat_path.write_text(json.dumps(cat))
    inv_path.write_text(json.dumps(inv))
    command = [
        sys.executable,
        "-m",
        "scripts.eea_catalog",
        "--catalog",
        str(cat_path),
        "--inventory",
        str(inv_path),
    ]
    if mode == "new_stream":
        path = tmp_path / "discovery.txt"
        path.write_text(f"ParquetFileUrl\n{URL}\n{NEW}\n")
        command += ["--discovery", str(path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    if mode == "geometry":
        assert report["crossCheckFindings"][0]["stationId"] == "RO0008R"
    else:
        assert report["missingChannels"] == []
        assert report["missingStreams"] == []
        assert report["unregisteredStreams"] == [NEW]


def test_discovery_parser_enforces_unique_url_count_before_collection():
    urls = [
        BASE + f"SPO-RO0008R_00008_{sequence}.parquet" for sequence in range(16_001)
    ]
    payload = ("ParquetFileUrl\n" + "\n".join(urls)).encode()
    assert len(payload) < 2 * 1024 * 1024
    with pytest.raises(ValueError, match="excessive URLs"):
        importer.parse_parquet_urls_payload(payload)


@pytest.mark.parametrize("kind", ["catalog", "inventory"])
def test_registry_entry_count_is_bounded(registries, kind):
    cat, inv = registries
    if kind == "catalog":
        cat["stations"] *= 2001
        with pytest.raises(ValueError, match="bounded list"):
            catalog.validate_station_catalog(cat)
    else:
        inv["channels"] *= 12_001
        with pytest.raises(ValueError, match="bounded list"):
            catalog.validate_channel_inventory(inv, cat)


@pytest.mark.parametrize("document", ["catalog", "inventory"])
def test_deeply_nested_json_is_rejected_without_traceback(tmp_path, document):
    cat_path, inv_path = tmp_path / "catalog.json", tmp_path / "inventory.json"
    cat_path.write_text("{}")
    inv_path.write_text("{}")
    target = cat_path if document == "catalog" else inv_path
    target.write_text("[" * 100_000 + "0" + "]" * 100_000)
    if document == "inventory":
        cat = {"schemaVersion": 1, "catalogVersion": 1, "stations": []}
        cat_path.write_text(json.dumps(cat))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eea_catalog",
            "--catalog",
            str(cat_path),
            "--inventory",
            str(inv_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Invalid catalog, inventory or discovery input.\n"
