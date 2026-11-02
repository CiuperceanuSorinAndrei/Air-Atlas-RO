import gzip
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import build_eea_candidates as builder
from scripts import import_eea as importer

SOURCE = (
    "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml"
)
BASE = f"https://{importer.PARQUET_HOST}/airquality-p/RO/"
URL = BASE + "SPO-RO0078A_00008_100.parquet"
OTHER = BASE + "SPO-RO9999A_00008_100.parquet"
XML = Path(__file__).parent / "fixtures/eea_reported_stations.xml"
NATIONAL = Path(__file__).resolve().parents[1] / "data/eea/candidates/2026-11-02"


def discovery(*urls):
    return ("ParquetFileUrl\n" + "\n".join(urls) + "\n").encode()


def build(body=None, urls=None, **kwargs):
    return builder.build_candidates(
        XML.read_bytes() if body is None else body,
        SOURCE,
        2025,
        discovery(URL) if urls is None else urls,
        **kwargs,
    )


def crosscheck():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [23.806239, 44.318569]},
                "properties": {
                    "AirQualityStationEoICode": "RO0078A",
                    "AQStationName": "DJ-1",
                },
            }
        ],
    }


def test_exact_provenance_separate_inventory_and_unresolved_identity():
    result = build(urls=discovery(OTHER, URL))
    catalog, inventory, report = (
        result[n]
        for n in (
            "catalog-candidate.json",
            "inventory-candidate.json",
            "review-report.json",
        )
    )
    assert len(catalog["stations"]) == 5
    dj = next(row for row in catalog["stations"] if row["stationId"] == "RO0078A")
    assert dj["latitude"] == 44.318569
    assert dj["longitude"] == 23.806239
    assert dj["metadataSourceSha256"] == hashlib.sha256(XML.read_bytes()).hexdigest()
    assert dj["metadataSourceUrl"] == SOURCE
    assert dj["metadataEnvelopeUrl"] == SOURCE.rsplit("/", 1)[0] + "/"
    assert dj["metadataDelivery"] == {
        "headerId": "D_ReportingHeader",
        "reportingPeriodStart": "2025-01-01T00:00:00+02:00",
        "reportingPeriodEnd": "2026-01-01T00:00:00+02:00",
    }
    assert inventory["channels"] == [
        {
            "stationId": "RO0078A",
            "pollutant": "NO2",
            "streams": [
                {"samplingPointId": "RO/SPO-RO0078A_00008_100", "sourceUrl": URL}
            ],
        }
    ]
    assert report["discoveryOnlyStations"] == ["RO9999A"]
    assert report["reconciliation"]["unregisteredStreams"] == [OTHER]
    assert "RO0150A" in report["xmlOnlyStations"]
    assert {
        "stationId": "RO0150A",
        "reportedActivityEnd": "2021-12-23T00:00:00+02:00",
    } in report["reportedActivityEnds"]
    assert report["approvalState"] == "candidate-only"
    assert report["requiresOwnerApproval"] is True
    assert report["discoveryCompleteness"] == "not-established-by-a-single-response"
    assert report["baselineDiff"] is None


def test_builder_is_offline_and_deterministic_in_discovery_order(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Candidate construction must have no network or snapshot writes")

    monkeypatch.setattr(importer, "read_response", forbidden)
    monkeypatch.setattr(importer, "write_observation_snapshot", forbidden)
    second = URL.replace("_100.parquet", "_200.parquet")
    left, right = build(urls=discovery(URL, second)), build(urls=discovery(second, URL))
    assert left["catalog-candidate.json"] == right["catalog-candidate.json"]
    assert left["inventory-candidate.json"] == right["inventory-candidate.json"]
    assert (
        left["review-report.json"]["inputs"]["discoverySha256"]
        != right["review-report.json"]["inputs"]["discoverySha256"]
    )


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_url",
        "duplicate_sequence",
        "empty",
        "bad_header",
        "foreign_host",
        "unknown_pollutant",
    ],
)
def test_invalid_discovery_rejected(case):
    bodies = {
        "duplicate_url": discovery(URL, URL),
        "duplicate_sequence": discovery(
            URL, URL.replace("_100.parquet", "_0100.parquet")
        ),
        "empty": discovery(),
        "bad_header": b"wrong\n",
        "foreign_host": discovery(URL.replace(importer.PARQUET_HOST, "evil.test")),
        "unknown_pollutant": discovery(URL.replace("_00008_", "_99999_")),
    }
    with pytest.raises((ValueError, TypeError)):
        build(urls=bodies[case])


@pytest.mark.parametrize(
    "case",
    [
        "namespace",
        "year",
        "fractional_period",
        "offset",
        "identity",
        "geometry",
        "duplicate",
        "doctype",
        "malformed",
        "no_station",
    ],
)
def test_invalid_authoritative_xml_never_produces_candidate(case):
    body = XML.read_bytes()
    if case == "namespace":
        body = body.replace(b"RO.ANPM.AQ", b"WRONG")
    elif case == "year":
        body = body.replace(b"2025-01-01", b"2024-01-01")
    elif case == "fractional_period":
        body = body.replace(b"2025-01-01T00:00:00", b"2025-01-01T00:00:00.000001")
    elif case == "offset":
        body = body.replace(b"2026-01-01T00:00:00+02:00", b"2026-01-01T00:00:00+03:00")
    elif case == "identity":
        body = body.replace(b"STA-RO0078A", b"STA-RO9999A")
    elif case == "geometry":
        body = body.replace(b"EPSG::4326", b"EPSG::3857")
    elif case == "duplicate":
        start = body.index(b"  <ns0:featureMember>\n    <ns1:AQD_Station")
        end = body.index(b"</ns0:featureMember>", start) + len(b"</ns0:featureMember>")
        body = body[:start] + body[start:end] + body[start:]
    elif case == "doctype":
        body = b'<!DOCTYPE root [<!ENTITY x "bad">]>\n' + body
    elif case == "malformed":
        body = b"<wrong>"
    elif case == "no_station":
        body = body.replace(b"AQD_Station", b"Other_Station")
    with pytest.raises((ValueError, TypeError)):
        build(body=body)


def test_crosscheck_preserves_authority_and_reports_distance_and_missing():
    data = crosscheck()
    data["features"][0]["geometry"]["coordinates"][0] += 0.01
    data["features"][0]["properties"]["AQStationName"] = "different"
    result = build(arcgis_body=json.dumps(data).encode())
    cat, report = result["catalog-candidate.json"], result["review-report.json"]
    row = next(r for r in cat["stations"] if r["stationId"] == "RO0078A")
    assert row["longitude"] == 23.806239
    assert row["arcgis"]["longitude"] == pytest.approx(23.816239)
    assert report["reconciliation"]["crossCheckFindings"][0]["distanceMeters"] > 10
    assert "RO0078A" not in report["missingCrossChecks"]
    assert "RO0008R" in report["missingCrossChecks"]
    assert report["nameDifferences"] == [
        {"stationId": "RO0078A", "xmlName": "DJ-1", "arcgisName": "different"}
    ]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "truncated",
        "truncated_properties",
        "bad_geometry",
        "nan",
        "wrong_country",
        "error",
        "duplicate_json",
    ],
)
def test_invalid_arcgis_is_not_silently_ignored(case):
    d = crosscheck()
    if case == "duplicate":
        d["features"] *= 2
    elif case == "truncated":
        d["exceededTransferLimit"] = True
    elif case == "truncated_properties":
        d["properties"] = {"exceededTransferLimit": True}
    elif case == "bad_geometry":
        d["features"][0]["geometry"]["type"] = "MultiPoint"
    elif case == "nan":
        d["features"][0]["geometry"]["coordinates"][0] = float("nan")
    elif case == "wrong_country":
        d["features"][0]["properties"]["AirQualityStationEoICode"] = "BG0001A"
    elif case == "error":
        d = {"error": {"code": 500}}
    body = (
        b'{"type":"FeatureCollection","type":"bad","features":[]}'
        if case == "duplicate_json"
        else json.dumps(d).encode()
    )
    with pytest.raises((ValueError, TypeError)):
        build(arcgis_body=body)


def test_previous_inventory_loss_and_metadata_changes_are_explicit_and_immutable():
    before = build(urls=discovery(URL, URL.replace("_00008_", "_00007_")))
    cat, inv = before["catalog-candidate.json"], before["inventory-candidate.json"]
    original = deepcopy(before)
    result = build(
        body=XML.read_bytes().replace(b"44.318569", b"44.318570"),
        previous_catalog=cat,
        previous_inventory=inv,
        catalog_version=2,
        inventory_version=2,
    )
    diff = result["review-report.json"]["baselineDiff"]
    assert diff["channels"]["removed"][0]["pollutant"] == "O3"
    assert diff["discoveryAgainstPrevious"]["missingChannels"] == [["RO0078A", "O3"]]
    assert any(
        row["before"]["latitude"] != row["after"]["latitude"]
        for row in diff["stations"]["changed"]
    )
    assert before == original


@pytest.mark.parametrize(
    "case", ["one_baseline", "old_version", "bool_version", "bad_source", "future_year"]
)
def test_invalid_baseline_version_or_source(case):
    first = build()
    kwargs = {}
    if case in {"one_baseline", "old_version"}:
        kwargs["previous_catalog"] = first["catalog-candidate.json"]
        if case == "old_version":
            kwargs["previous_inventory"] = first["inventory-candidate.json"]
    elif case == "bool_version":
        kwargs["catalog_version"] = True
    with pytest.raises((ValueError, TypeError)):
        if case == "bad_source":
            builder.build_candidates(
                XML.read_bytes(), SOURCE + "?q=x", 2025, discovery(URL)
            )
        elif case == "future_year":
            builder.build_candidates(XML.read_bytes(), SOURCE, 9999, discovery(URL))
        else:
            build(**kwargs)


def test_cli_no_clobber_and_invalid_input_does_not_create_output(tmp_path):
    urls, output = tmp_path / "discovery.txt", tmp_path / "candidate"
    urls.write_bytes(discovery(URL))
    command = [
        sys.executable,
        "-m",
        "scripts.build_eea_candidates",
        "--xml",
        str(XML),
        "--source-url",
        SOURCE,
        "--reporting-year",
        "2025",
        "--discovery",
        str(urls),
        "--output",
        str(output),
    ]
    good = subprocess.run(command, capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    files = {p.name: p.read_bytes() for p in output.iterdir()}
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode == 1
    assert {p.name: p.read_bytes() for p in output.iterdir()} == files
    absent = tmp_path / "absent"
    urls.write_bytes(b"bad")
    bad = subprocess.run([*command[:-1], str(absent)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert not absent.exists()
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    urls.write_bytes(discovery(URL))
    bad = subprocess.run([*command[:-1], str(dangling)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert dangling.is_symlink()
    assert not (tmp_path / "missing").exists()


def test_real_national_capture_reconstructs_all_candidates_byte_for_byte():
    with gzip.open(NATIONAL / "dataflow-d.xml.gz", "rb") as stream:
        xml_body = stream.read(32 * 1024 * 1024 + 1)
    documents = builder.build_candidates(
        xml_body,
        SOURCE,
        2025,
        (NATIONAL / "discovery.txt").read_bytes(),
        arcgis_body=(NATIONAL / "arcgis.geojson").read_bytes(),
    )
    for name, document in documents.items():
        assert builder.encode(document) == (NATIONAL / name).read_bytes()
    ns = importer.XML_NAMESPACES
    root = ET.fromstring(xml_body)
    expected = {
        node.findtext("aqd:EUStationCode", namespaces=ns): (
            node.findtext("ef:name", namespaces=ns),
            [
                float(value)
                for value in node.findtext(
                    "ef:geometry/gml:Point/gml:pos", namespaces=ns
                ).split()
            ],
        )
        for node in root.findall("gml:featureMember/aqd:AQD_Station", ns)
    }
    actual = documents["catalog-candidate.json"]["stations"]
    assert len(expected) == len(actual) == 210
    for row in actual:
        assert (row["stationName"], [row["latitude"], row["longitude"]]) == expected[
            row["stationId"]
        ]
    report = documents["review-report.json"]
    assert report["counts"] == {
        "xmlStations": 210,
        "discoveryStations": 180,
        "discoveryStreams": 717,
        "candidateChannels": 717,
        "candidateStreams": 717,
        "arcgisStations": 196,
    }
    assert report["discoveryOnlyStations"] == []
    assert len(report["missingCrossChecks"]) == 14
    assert len(report["xmlOnlyStations"]) == 30
    assert report["reconciliation"]["crossCheckFindings"] == []


def test_capture_records_exact_requests_and_original_bytes(monkeypatch):
    from scripts import capture_eea_candidates as capture

    responses = [
        XML.read_bytes(),
        discovery(URL),
        json.dumps(crosscheck()).encode(),
        b"<html>Official envelope</html>",
    ]
    requests = []

    def read(request, limit):
        requests.append((request, limit))
        return responses[len(requests) - 1], '"native-etag"'

    monkeypatch.setattr(importer, "read_http_response", read)
    files = capture.capture_sources(SOURCE, 2025)
    manifest = json.loads(files["capture.json"])
    assert [r[1] for r in requests] == [
        32 * 1024 * 1024,
        2 * 1024 * 1024,
        8 * 1024 * 1024,
        2 * 1024 * 1024,
    ]
    body = json.loads(requests[1][0].data)
    assert body["dataset"] == 1
    assert body["countries"] == ["RO"]
    assert set(body["pollutants"]) == {"SO2", "PM10", "O3", "NO2", "CO", "PM2.5"}
    assert manifest[1]["method"] == "POST"
    for item, response in zip(manifest, responses, strict=True):
        assert files[item["file"]] == response
        assert item["sha256"] == hashlib.sha256(response).hexdigest()
        assert item["bytes"] == len(response)
        assert item["etag"] == '"native-etag"'
        assert (
            importer.parse_timestamp(item["retrievedAt"]).utcoffset().total_seconds()
            == 0
        )


def test_staging_failure_and_rename_failure_never_expose_partial_candidates(
    tmp_path, monkeypatch
):
    documents = build()
    output = tmp_path / "output"
    original = Path.open

    def fail(path, *args, **kwargs):
        if path.name == "inventory-candidate.json":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail)
        with pytest.raises(OSError):
            builder.write_files(
                {n: builder.encode(d) for n, d in documents.items()}, output
            )
    assert list(tmp_path.iterdir()) == []

    def fail_rename(*args):
        raise OSError("rename failure")

    monkeypatch.setattr(builder.os, "rename", fail_rename)
    with pytest.raises(OSError):
        builder.write_files(
            {n: builder.encode(d) for n, d in documents.items()}, output
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("input_kind", ["xml", "arcgis", "discovery"])
def test_source_byte_limits(input_kind):
    if input_kind == "xml":
        with pytest.raises(ValueError):
            importer.parse_dataflow_d_report(b" " * (32 * 1024 * 1024 + 1), 2025)
    elif input_kind == "arcgis":
        with pytest.raises(ValueError):
            builder.parse_arcgis(b" " * (8 * 1024 * 1024 + 1))
    else:
        with pytest.raises(ValueError):
            build(urls=b" " * (2 * 1024 * 1024 + 1))


def test_legacy_network_parser_returns_same_metadata_and_tuple_contract(monkeypatch):
    importer.fetch_dataflow_d_report.cache_clear()
    monkeypatch.setattr(
        importer, "read_response", lambda request, limit: XML.read_bytes()
    )
    try:
        stations, contents, digest = importer.fetch_dataflow_d_report(SOURCE, 2025)
        legacy = importer.resolve_dataflow_d_station(
            stations, contents, "RO0078A", SOURCE, 2025, digest
        )
        assert legacy == {
            "stationId": "RO0078A",
            "stationName": "DJ-1",
            "latitude": 44.318569,
            "longitude": 23.806239,
            "metadataSourceUrl": SOURCE,
            "metadataReportingYear": 2025,
            "metadataSourceSha256": hashlib.sha256(XML.read_bytes()).hexdigest(),
        }
    finally:
        importer.fetch_dataflow_d_report.cache_clear()


def test_real_capture_manifest_matches_every_archived_response():
    manifest = json.loads((NATIONAL / "capture.json").read_bytes())
    for item in manifest:
        if item["file"] == "dataflow-d.xml":
            with gzip.open(NATIONAL / "dataflow-d.xml.gz", "rb") as stream:
                body = stream.read(32 * 1024 * 1024 + 1)
        else:
            body = (NATIONAL / item["file"]).read_bytes()
        assert len(body) == item["bytes"]
        assert hashlib.sha256(body).hexdigest() == item["sha256"]


@pytest.mark.parametrize("failed_request", [1, 2, 3, 4])
def test_capture_cli_partial_network_failure_never_exposes_output(
    tmp_path, monkeypatch, failed_request
):
    from scripts import capture_eea_candidates as capture

    requests = []
    responses = [
        XML.read_bytes(),
        discovery(URL),
        json.dumps(crosscheck()).encode(),
        b"<html>envelope</html>",
    ]

    def read(request, limit):
        requests.append(request.full_url)
        if len(requests) == failed_request:
            raise TimeoutError("controlled fixture timeout")
        return responses[len(requests) - 1], None

    output = tmp_path / "capture"
    monkeypatch.setattr(importer, "read_http_response", read)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_eea_candidates",
            "--source-url",
            SOURCE,
            "--reporting-year",
            "2025",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as error:
        capture.main()
    assert error.value.code == 1
    assert len(requests) == failed_request
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory", "dangling_symlink"])
def test_capture_cli_existing_output_is_preserved_before_network(
    tmp_path, monkeypatch, kind
):
    from scripts import capture_eea_candidates as capture

    output = tmp_path / "capture"
    if kind == "directory":
        output.mkdir()
        (output / "owner.txt").write_text("unchanged")
    else:
        output.symlink_to(tmp_path / "absent")
    monkeypatch.setattr(
        importer,
        "read_http_response",
        lambda *args: pytest.fail("Network must not run for existing output"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_eea_candidates",
            "--source-url",
            SOURCE,
            "--reporting-year",
            "2025",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as error:
        capture.main()
    assert error.value.code == 1
    if kind == "directory":
        assert (output / "owner.txt").read_text() == "unchanged"
    else:
        assert output.is_symlink() and not (tmp_path / "absent").exists()
