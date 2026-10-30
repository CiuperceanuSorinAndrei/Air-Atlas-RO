import hashlib
import json
import urllib.error
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from scripts import import_eea as importer

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE = json.loads((FIXTURES / "eea_reported_stations.json").read_text())
XML = (FIXTURES / "eea_reported_stations.xml").read_bytes()
URL = CAPTURE["sourceXmlUrl"]
NS = importer.XML_NAMESPACES


@pytest.fixture(autouse=True)
def clear_report_cache():
    importer.fetch_dataflow_d_report.cache_clear()
    yield
    importer.fetch_dataflow_d_report.cache_clear()


def install(monkeypatch, xml=XML, pages=None):
    requests = []
    pages = deepcopy(CAPTURE["pages"] if pages is None else pages)

    def read(request, limit):
        requests.append(request.full_url)
        if request.full_url == URL:
            assert limit == 32 * 1024 * 1024
            if isinstance(xml, Exception):
                raise xml
            return xml
        assert limit == 8 * 1024 * 1024
        if "/MapServer/" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url, 502, "Unavailable", None, None
            )
        if "/init?" in request.full_url:
            return json.dumps({"Request": {"Page": 0, "RequestFilter": {}}}).encode()
        if "/filter?" in request.full_url:
            return json.dumps(pages[json.loads(request.data)["Page"]]).encode()
        pytest.fail(f"Unexpected request: {request.full_url}")

    monkeypatch.setattr(importer, "read_response", read)
    return requests


def rows():
    return [
        deepcopy(row) for page in CAPTURE["pages"] for row in page["Preview"]["Rows"]
    ]


def test_captured_conflict_resolves_through_xml_and_caches_provenance(
    tmp_path, monkeypatch
):
    requests = install(monkeypatch)
    cache = tmp_path / "metadata.json"
    result = importer.get_station_metadata("RO0008R", cache)
    assert result == {
        "stationId": "RO0008R",
        "stationName": "EM-3",
        "longitude": 25.134839,
        "latitude": 47.324686,
        "metadataSourceUrl": URL,
        "metadataReportingYear": 2025,
        "metadataSourceSha256": hashlib.sha256(XML).hexdigest(),
    }
    assert json.loads(cache.read_text())["RO0008R"]["metadata"] == result
    assert len(requests) == 5
    assert requests[-1] == URL
    assert importer.get_station_metadata("RO0008R", cache) == result
    assert len(requests) == 5


@pytest.mark.parametrize(
    ("station_id", "name", "longitude", "latitude"),
    [
        ("RO0008R", "EM-3", 25.134839, 47.324686),
        ("RO0078A", "DJ-1", 23.806239, 44.318569),
        ("RO0141A", "GL-1", 28.018594, 45.420411),
        ("RO0143A", "GL-3", 28.034406, 45.474156),
        ("RO0150A", "GJ-1", 23.273978, 45.050194),
    ],
)
def test_captured_xml_has_exact_station_coordinates(
    monkeypatch, station_id, name, longitude, latitude
):
    install(monkeypatch)
    station_rows = rows()
    for row in station_rows:
        row["AirQualityStationEoICode"] = station_id
    result = importer.resolve_dataflow_d_station_metadata(station_rows, station_id)
    assert (result["stationName"], result["longitude"], result["latitude"]) == (
        name,
        longitude,
        latitude,
    )


def test_shared_document_is_downloaded_once_for_multiple_stations(monkeypatch):
    requests = install(monkeypatch)
    for station in ["RO0008R", "RO0078A", "RO0143A"]:
        station_rows = rows()
        for row in station_rows:
            row["AirQualityStationEoICode"] = station
        importer.resolve_dataflow_d_station_metadata(station_rows, station)
    assert requests == [URL]


@pytest.mark.parametrize(
    "case",
    [
        "missing_year",
        "invalid_year",
        "future_year",
        "wrong_namespace",
        "missing_source",
        "invalid_source_type",
        "competing_documents",
        "external_host",
        "userinfo",
        "query",
        "fragment",
        "wrong_path",
        "nonstandard_port",
    ],
)
def test_untrusted_document_selection_fails_without_network(monkeypatch, case):
    requests = install(monkeypatch)
    station_rows = rows()
    current = next(row for row in station_rows if row["ReportingYear"] == "2025")
    if case == "missing_year":
        del current["ReportingYear"]
    elif case == "invalid_year":
        current["ReportingYear"] = True
    elif case == "future_year":
        current["ReportingYear"] = "9999"
    elif case == "wrong_namespace":
        current["B2G_Namespace"] = "OTHER"
    elif case == "missing_source":
        del current["SourceDataURL"]
    elif case == "invalid_source_type":
        current["SourceDataURL"] = [URL]
    elif case == "competing_documents":
        current["SourceDataURL"] = URL.replace("updateV2", "updateV3")
    else:
        urls = {
            "external_host": "https://example.com/ro/eu/aqd/d/envag8edw/data.xml",
            "userinfo": URL.replace("https://", "https://user@"),
            "query": URL + "?override=1",
            "fragment": URL + "#station",
            "wrong_path": URL.replace("/ro/", "/fr/"),
            "nonstandard_port": URL.replace(".europa.eu/", ".europa.eu:8443/"),
        }
        for row in station_rows:
            if row["ReportingYear"] == "2025":
                row["SourceDataURL"] = urls[case]
    with pytest.raises((ValueError, TypeError)):
        importer.resolve_dataflow_d_station_metadata(station_rows, "RO0008R")
    assert not requests


@pytest.mark.parametrize(
    "case",
    [
        "missing_station",
        "duplicate_station",
        "wrong_eoi",
        "wrong_local_id",
        "wrong_gml_id",
        "wrong_namespace",
        "missing_content",
        "wrong_report_namespace",
        "wrong_year",
        "wrong_end",
        "fractional_begin",
        "fractional_end",
        "period_offset_mismatch",
        "naive_period",
        "duplicate_header",
        "wrong_root",
        "wrong_crs",
        "missing_geometry",
        "duplicate_point",
        "wrong_dimension",
        "position_crs_override",
        "point_dimension_override",
        "extra_coordinate",
        "nan",
        "infinity",
        "out_of_range",
        "empty_name",
        "malformed",
        "doctype",
        "entity",
        "utf16",
        "unavailable",
    ],
)
def test_untrusted_xml_preserves_existing_cache(tmp_path, monkeypatch, case):
    root = ET.fromstring(XML)
    header = root.find("gml:featureMember/aqd:AQD_ReportingHeader", NS)
    member = next(
        m
        for m in root
        if m.find("aqd:AQD_Station/aqd:EUStationCode", NS) is not None
        and m.findtext("aqd:AQD_Station/aqd:EUStationCode", namespaces=NS) == "RO0008R"
    )
    station = member.find("aqd:AQD_Station", NS)
    identity = station.find("ef:inspireId/base:Identifier", NS)
    point = station.find("ef:geometry/gml:Point", NS)
    pos = point.find("gml:pos", NS)
    if case == "missing_station":
        root.remove(member)
    elif case == "duplicate_station":
        root.append(deepcopy(member))
    elif case == "wrong_eoi":
        station.find("aqd:EUStationCode", NS).text = "RO9999A"
    elif case == "wrong_local_id":
        identity.find("base:localId", NS).text = "STA-RO9999A"
    elif case == "wrong_gml_id":
        station.set(f"{{{NS['gml']}}}id", "STA-RO9999A")
    elif case == "wrong_namespace":
        identity.find("base:namespace", NS).text = "OTHER"
    elif case == "missing_content":
        for content in header.findall("aqd:content", NS):
            header.remove(content)
    elif case == "wrong_report_namespace":
        header.find("aqd:inspireId/base:Identifier/base:namespace", NS).text = "OTHER"
    elif case in ["wrong_year", "naive_period"]:
        header.find("aqd:reportingPeriod/gml:TimePeriod/gml:beginPosition", NS).text = (
            "2024-01-01T00:00:00+02:00"
            if case == "wrong_year"
            else "2025-01-01T00:00:00"
        )
    elif case == "wrong_end":
        header.find(
            "aqd:reportingPeriod/gml:TimePeriod/gml:endPosition", NS
        ).text = "2027-01-01T00:00:00+02:00"
    elif case in ["fractional_begin", "fractional_end", "period_offset_mismatch"]:
        position = "beginPosition" if case == "fractional_begin" else "endPosition"
        stamp = header.find("aqd:reportingPeriod/gml:TimePeriod/gml:" + position, NS)
        if case == "period_offset_mismatch":
            stamp.text = "2026-01-01T00:00:00+03:00"
        else:
            stamp.text = stamp.text.replace("T00:00:00", "T00:00:00.000001")
    elif case == "duplicate_header":
        root.append(deepcopy(root[0]))
    elif case == "wrong_root":
        root.tag = "FeatureCollection"
    elif case == "wrong_crs":
        point.set("srsName", "urn:ogc:def:crs:EPSG::3857")
    elif case == "missing_geometry":
        station.remove(station.find("ef:geometry", NS))
    elif case == "duplicate_point":
        station.find("ef:geometry", NS).append(deepcopy(point))
    elif case == "wrong_dimension":
        pos.set("srsDimension", "3")
    elif case == "position_crs_override":
        pos.set("srsName", "urn:ogc:def:crs:EPSG::3857")
    elif case == "point_dimension_override":
        point.set("srsDimension", "3")
    elif case == "extra_coordinate":
        pos.text += " 900"
    elif case in ["nan", "infinity", "out_of_range"]:
        pos.text = {"nan": "nan 25", "infinity": "47 inf", "out_of_range": "91 25"}[
            case
        ]
    elif case == "empty_name":
        station.find("ef:name", NS).text = " "
    xml = ET.tostring(root)
    if case == "malformed":
        xml = b"<broken"
    elif case in ["doctype", "entity"]:
        xml = (
            b'<!DOCTYPE x [<!ENTITY x "expanded">]>'
            if case == "entity"
            else b"<!DOCTYPE x>"
        ) + xml
    elif case == "utf16":
        xml = xml.decode().encode("utf-16")
    elif case == "unavailable":
        xml = urllib.error.URLError("Unavailable")
    install(monkeypatch, xml=xml)
    cache = tmp_path / "metadata.json"
    previous = b'{"RO0008R":{"fetchedAt":"2000-01-01T00:00:00+00:00","metadata":{"stationId":"RO0008R","stationName":"previous","longitude":25,"latitude":47}}}'
    cache.write_bytes(previous)
    with pytest.raises((ValueError, TypeError, urllib.error.URLError)):
        importer.get_station_metadata("RO0008R", cache)
    assert cache.read_bytes() == previous


def test_document_period_cannot_be_reused_for_a_different_reporting_year(monkeypatch):
    install(monkeypatch)
    importer.fetch_dataflow_d_report(URL, 2025)
    with pytest.raises(ValueError, match="period mismatch"):
        importer.fetch_dataflow_d_report(URL, 2024)


@pytest.mark.parametrize(
    "failure",
    [
        b"<broken",
        XML.replace(b"2025-01-01", b"2024-01-01"),
        urllib.error.URLError("temporary outage"),
    ],
)
def test_failed_document_is_not_cached_and_a_later_attempt_can_recover(
    tmp_path, monkeypatch, failure
):
    first = install(monkeypatch, xml=failure)
    cache = tmp_path / "metadata.json"
    with pytest.raises((ValueError, urllib.error.URLError)):
        importer.get_station_metadata("RO0008R", cache)
    assert not cache.exists()
    assert first.count(URL) == 1
    second = install(monkeypatch)
    result = importer.get_station_metadata("RO0008R", cache)
    assert result["stationName"] == "EM-3"
    assert second.count(URL) == 1


@pytest.mark.parametrize(
    "path",
    [
        "ef:name",
        "aqd:EUStationCode",
        "ef:inspireId/base:Identifier/base:localId",
        "ef:inspireId/base:Identifier/base:namespace",
        "ef:geometry",
    ],
)
def test_duplicate_station_fields_are_not_silently_selected(monkeypatch, path):
    root = ET.fromstring(XML)
    station = next(
        station
        for station in root.findall("gml:featureMember/aqd:AQD_Station", NS)
        if station.findtext("aqd:EUStationCode", namespaces=NS) == "RO0008R"
    )
    value = station.find(path, NS)
    parent_path = path.rpartition("/")[0]
    parent = station.find(parent_path, NS) if parent_path else station
    parent.append(deepcopy(value))
    install(monkeypatch, xml=ET.tostring(root))
    with pytest.raises(ValueError):
        importer.resolve_dataflow_d_station_metadata(rows(), "RO0008R")


def raw_observation(pollutant=1):
    return {
        "Samplingpoint": f"RO/SPO-RO0008R_{pollutant:05d}_100",
        "Pollutant": pollutant,
        "Value": 0.19521 if pollutant == 10 else 13.108,
        "Unit": "mg.m-3" if pollutant == 10 else "ug.m-3",
        "AggType": "hour",
        "Start": datetime.fromisoformat("2026-10-28T06:00:00"),
        "End": datetime.fromisoformat("2026-10-28T07:00:00"),
        "ResultTime": datetime.fromisoformat("2026-10-28T07:02:34"),
        "Validity": 1,
        "Verification": 3,
    }


@pytest.mark.parametrize("pollutant", list(importer.POLLUTANT_NAMES))
def test_import_preserves_measurement_and_time_semantics_after_xml_resolution(
    tmp_path, monkeypatch, pollutant
):
    install(monkeypatch)
    raw = raw_observation(pollutant)
    original = deepcopy(raw)
    monkeypatch.setattr(importer, "fetch_latest_observation", lambda url: raw)
    url = f"https://{importer.PARQUET_HOST}/airquality-p/RO/SPO-RO0008R_{pollutant:05d}_100.parquet"
    result = importer.import_observation(url, tmp_path / "metadata.json")
    assert raw == original
    assert result["stationName"] == "EM-3"
    assert (result["longitude"], result["latitude"]) == (25.134839, 47.324686)
    assert result["value"] == raw["Value"]
    assert result["unit"] == raw["Unit"]
    assert result["pollutant"] == importer.POLLUTANT_NAMES[pollutant]
    assert result["observedFrom"] == "2026-10-28T06:00:00+01:00"
    assert result["observedTo"] == "2026-10-28T07:00:00+01:00"
    assert result["validity"] == 1
    assert result["verification"] == 3
    importer.validate_observation(result)


def test_cli_preserves_snapshot_when_xml_cannot_resolve_conflict(tmp_path, monkeypatch):
    install(monkeypatch, xml=b"<broken")
    path = tmp_path / "src/data/observations.json"
    path.parent.mkdir(parents=True)
    previous = b'{"previousSnapshot":true}'
    path.write_bytes(previous)
    url = (
        f"https://{importer.PARQUET_HOST}/airquality-p/RO/SPO-RO0008R_00001_100.parquet"
    )
    monkeypatch.setattr(importer, "__file__", str(tmp_path / "scripts/import_eea.py"))
    monkeypatch.setattr(importer.sys, "argv", ["import_eea"])
    monkeypatch.setattr(importer, "fetch_parquet_urls", lambda *args: [url])
    monkeypatch.setattr(
        importer, "fetch_latest_observation", lambda url: raw_observation()
    )
    import_observation = importer.import_observation
    monkeypatch.setattr(
        importer,
        "import_observation",
        lambda url: import_observation(url, tmp_path / "metadata.json"),
    )
    with pytest.raises(
        ValueError, match="Snapshot import blocked.*Invalid Dataflow D XML"
    ):
        importer.main()
    assert path.read_bytes() == previous
    assert not (tmp_path / "metadata.json").exists()
