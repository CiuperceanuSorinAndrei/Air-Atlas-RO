import gzip
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts import build_eea_candidates as builder
from scripts import eea_registry as runtime
from scripts import import_eea as importer

SOURCE = (
    "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml"
)
BASE = f"https://{importer.PARQUET_HOST}/airquality-p/RO/"
URL = BASE + "SPO-RO0078A_00008_100.parquet"
SECOND = URL.replace("_00008_", "_00007_")
NATIONAL = Path(__file__).resolve().parents[1] / "data/eea"


def discovery(*urls):
    return ("ParquetFileUrl\n" + "\n".join(urls) + "\n").encode()


def source_fixture():
    ns = importer.XML_NAMESPACES
    root = ET.fromstring(
        (Path(__file__).parent / "fixtures/eea_reported_stations.xml").read_bytes()
    )
    header = root.find("gml:featureMember/aqd:AQD_ReportingHeader", ns)
    for code in (8, 7):
        native = f"SPO-RO0078A_{code:05d}_100"
        ET.SubElement(
            header,
            f"{{{ns['aqd']}}}content",
            {f"{{{ns['xlink']}}}href": "RO.ANPM.AQ/" + native},
        )
        member = ET.SubElement(root, f"{{{ns['gml']}}}featureMember")
        point = ET.SubElement(
            member, f"{{{ns['aqd']}}}AQD_SamplingPoint", {f"{{{ns['gml']}}}id": native}
        )
        ident = ET.SubElement(
            ET.SubElement(point, f"{{{ns['ef']}}}inspireId"),
            f"{{{ns['base']}}}Identifier",
        )
        ET.SubElement(ident, f"{{{ns['base']}}}localId").text = native
        ET.SubElement(ident, f"{{{ns['base']}}}namespace").text = "RO.ANPM.AQ"
        ET.SubElement(
            point,
            f"{{{ns['ef']}}}broader",
            {f"{{{ns['xlink']}}}href": "RO.ANPM.AQ/STA-RO0078A"},
        )
        capability = ET.SubElement(
            ET.SubElement(point, f"{{{ns['ef']}}}observingCapability"),
            f"{{{ns['ef']}}}ObservingCapability",
        )
        ET.SubElement(
            capability,
            f"{{{ns['ef']}}}observedProperty",
            {
                f"{{{ns['xlink']}}}href": f"http://dd.eionet.europa.eu/vocabulary/aq/pollutant/{code}"
            },
        )
    return root


def documents(root=None):
    body = ET.tostring(source_fixture() if root is None else root, encoding="utf-8")
    candidates = builder.build_candidates(body, SOURCE, 2025, discovery(URL, SECOND))
    return (
        body,
        candidates["catalog-candidate.json"],
        candidates["inventory-candidate.json"],
    )


@pytest.fixture
def installed(tmp_path):
    body, cat, inv = documents()
    docs = runtime.prepare_registry(body, cat, inv)
    folder = tmp_path / "registry"
    builder.write_files(
        {name: builder.encode(doc) for name, doc in docs.items()}, folder
    )
    return folder, cat, inv


def raw(url=URL):
    end = datetime.now(importer.EEA_TIMEZONE).replace(
        tzinfo=None, microsecond=0
    ) - timedelta(hours=1)
    return {
        "Value": 20,
        "AggType": "hour",
        "Pollutant": int(importer.parse_series_url(url)[1]),
        "Samplingpoint": "RO/" + Path(url).stem,
        "Unit": "ug.m-3",
        "Validity": 1,
        "Verification": 1,
        "Start": end - timedelta(hours=1),
        "End": end,
        "ResultTime": end,
    }


def test_review_all_native_sampling_points_and_exact_station_metadata():
    body, cat, inv = documents()
    review = runtime.review_registry(body, cat, inv)
    assert review["verifiedStations"] == 5
    assert review["verifiedSamplingPoints"] == 2
    assert review["scope"] == "local-trial"
    assert review["productionActivationAuthorized"] is False
    assert "RO0150A" in review["reportedEndedStations"]
    changed = deepcopy(cat)
    changed["stations"][0]["latitude"] += 0.001
    with pytest.raises(ValueError, match="differs"):
        runtime.review_registry(body, changed, inv)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "localid",
        "namespace",
        "parent",
        "pollutant",
        "header",
        "ended_point",
        "ended_station",
    ],
)
def test_authoritative_sampling_point_contradictions_block_review(case):
    root = source_fixture()
    ns = importer.XML_NAMESPACES
    point = root.find("gml:featureMember/aqd:AQD_SamplingPoint", ns)
    if case == "missing":
        point.set(f"{{{ns['gml']}}}id", "different")
    elif case == "duplicate":
        root.find("gml:featureMember", ns).append(deepcopy(point))
    elif case == "localid":
        point.find("ef:inspireId/base:Identifier/base:localId", ns).text = "different"
    elif case == "namespace":
        point.find("ef:inspireId/base:Identifier/base:namespace", ns).text = "WRONG"
    elif case == "parent":
        point.find("ef:broader", ns).set(
            f"{{{ns['xlink']}}}href", "RO.ANPM.AQ/STA-RO0150A"
        )
    elif case == "pollutant":
        point.find(
            "ef:observingCapability/ef:ObservingCapability/ef:observedProperty", ns
        ).set(
            f"{{{ns['xlink']}}}href",
            "http://dd.eionet.europa.eu/vocabulary/aq/pollutant/5",
        )
    elif case == "header":
        header = root.find("gml:featureMember/aqd:AQD_ReportingHeader", ns)
        for content in list(header):
            if (
                content.get(f"{{{ns['xlink']}}}href")
                == "RO.ANPM.AQ/SPO-RO0078A_00008_100"
            ):
                header.remove(content)
    elif case in {"ended_point", "ended_station"}:
        node = (
            point
            if case == "ended_point"
            else root.find("gml:featureMember/aqd:AQD_Station", ns)
        )
        period = ET.SubElement(
            ET.SubElement(
                ET.SubElement(
                    ET.SubElement(node, f"{{{ns['ef']}}}operationalActivityPeriod"),
                    f"{{{ns['ef']}}}OperationalActivityPeriod",
                ),
                f"{{{ns['ef']}}}activityTime",
            ),
            f"{{{ns['gml']}}}TimePeriod",
        )
        ET.SubElement(
            period, f"{{{ns['gml']}}}endPosition"
        ).text = "2025-06-01T00:00:00+02:00"
    body, cat, inv = documents(root)
    with pytest.raises(ValueError):
        runtime.review_registry(body, cat, inv)


@pytest.mark.parametrize("file", ["catalog.json", "inventory.json", "review.json"])
def test_pinned_file_tampering_blocks_before_observation_io(
    installed, tmp_path, monkeypatch, file
):
    folder, _, _ = installed
    p = folder / file
    p.write_bytes(p.read_bytes() + b" ")
    monkeypatch.setattr(
        importer,
        "fetch_latest_observation",
        lambda url: pytest.fail("Unexpected network"),
    )
    with pytest.raises(ValueError, match="hash"):
        runtime.collect_registry_run(
            folder / "manifest.json",
            tmp_path / "out",
            discovery_body=discovery(URL, SECOND),
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "case",
    [
        "candidate",
        "production_scope",
        "bool_version",
        "wrong_version",
        "unknown_file",
        "bad_digest",
        "duplicate_key",
        "blocked_review",
    ],
)
def test_invalid_or_unreviewed_manifest_rejected(installed, case):
    folder, _, _ = installed
    p = folder / "manifest.json"
    doc = json.loads(p.read_bytes())
    if case == "candidate":
        doc["status"] = "candidate-only"
    elif case == "production_scope":
        doc["scope"] = "production"
    elif case == "bool_version":
        doc["catalogVersion"] = True
    elif case == "wrong_version":
        doc["inventoryVersion"] = 2
    elif case == "unknown_file":
        doc["files"]["../outside.json"] = "a" * 64
    elif case == "bad_digest":
        doc["files"]["catalog.json"] = "bad"
    elif case == "blocked_review":
        r = json.loads((folder / "review.json").read_bytes())
        r["blockingFindings"] = ["conflict"]
        body = builder.encode(r)
        (folder / "review.json").write_bytes(body)
        doc["files"]["review.json"] = hashlib.sha256(body).hexdigest()
    p.write_bytes(
        b'{"schemaVersion":1,"schemaVersion":1}'
        if case == "duplicate_key"
        else builder.encode(doc)
    )
    with pytest.raises((ValueError, TypeError)):
        runtime.load_registry(p)


def test_registry_mode_uses_no_dynamic_metadata_and_reports_every_absent_reading(
    installed, tmp_path, monkeypatch
):
    folder, _, _ = installed
    extra = URL.replace("_100.parquet", "_200.parquet")
    calls = []

    def latest(url):
        calls.append(url)
        if url == SECOND:
            raise importer.NoValidObservationsError("decoded empty")
        return raw(url)

    def forbidden(*args, **kwargs):
        pytest.fail("Dynamic metadata/cache called")

    monkeypatch.setattr(importer, "fetch_latest_observation", latest)
    for name in (
        "get_station_metadata",
        "fetch_station_metadata",
        "fetch_dataflow_d_station_metadata",
        "load_station_metadata_cache",
    ):
        monkeypatch.setattr(importer, name, forbidden)
    output = tmp_path / "out"
    report = runtime.collect_registry_run(
        folder / "manifest.json", output, discovery_body=discovery(URL, SECOND, extra)
    )
    assert set(calls) == {URL, SECOND}
    assert report["approvedChannels"] == 2 and report["eligibleChannels"] == 1
    assert report["channelsWithoutObservation"] == [
        {"stationId": "RO0078A", "pollutant": "O3", "reason": "noEligibleObservation"}
    ]
    assert report["discovery"]["unregisteredStreams"] == [extra]
    assert report["controlledDegradationImplemented"] is False
    snapshot = json.loads((output / "observations.json").read_bytes())
    assert snapshot["importSummary"] == {
        "attempted": 2,
        "imported": 1,
        "skipped": 1,
        "failed": 0,
    }
    assert snapshot["observations"][0]["stationName"] == "DJ-1"
    assert len(runtime.load_registry(folder / "manifest.json")[1]["channels"]) == 2


@pytest.mark.parametrize(
    "case",
    [
        "missing_discovery",
        "hard_error",
        "all_empty",
        "recent_loss",
        "interval_regression",
    ],
)
def test_failures_preserve_previous_snapshot_and_do_not_expose_output(
    installed, tmp_path, monkeypatch, case
):
    folder, cat, _ = installed
    station = next(s for s in cat["stations"] if s["stationId"] == "RO0078A")
    previous = {
        "observations": [
            importer.normalize_observation(raw(url), station, url)
            for url in (URL, SECOND)
        ],
        "importSummary": {"attempted": 2, "imported": 2, "skipped": 0, "failed": 0},
    }
    path = tmp_path / "previous.json"
    body = builder.encode(previous)
    path.write_bytes(body)

    def latest(url):
        if case == "hard_error":
            raise ValueError("identity failure")
        if case == "all_empty" or (case == "recent_loss" and url == SECOND):
            raise importer.NoValidObservationsError("empty")
        row = raw(url)
        if case == "interval_regression":
            for field in ("Start", "End", "ResultTime"):
                row[field] -= timedelta(hours=2)
        return row

    monkeypatch.setattr(importer, "fetch_latest_observation", latest)
    monkeypatch.setattr(
        importer,
        "fetch_parquet_table",
        lambda url: (_ for _ in ()).throw(ValueError("unconfirmed regression")),
    )
    with pytest.raises(ValueError):
        runtime.collect_registry_run(
            folder / "manifest.json",
            tmp_path / "out",
            discovery_body=discovery(URL)
            if case == "missing_discovery"
            else discovery(URL, SECOND),
            previous_snapshot=path,
        )
    assert path.read_bytes() == body
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".eea-*"))


def test_output_no_clobber_and_bad_previous_fail_before_network(
    installed, tmp_path, monkeypatch
):
    folder, _, _ = installed
    monkeypatch.setattr(
        importer,
        "fetch_latest_observation",
        lambda url: pytest.fail("Network before validation"),
    )
    output = tmp_path / "out"
    output.mkdir()
    (output / "owner.txt").write_text("keep")
    with pytest.raises(ValueError):
        runtime.collect_registry_run(folder / "manifest.json", output)
    assert (output / "owner.txt").read_text() == "keep"
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises((ValueError, TypeError)):
        runtime.collect_registry_run(
            folder / "manifest.json", tmp_path / "new", previous_snapshot=bad
        )
    assert not (tmp_path / "new").exists()


def test_default_cli_and_opt_in_guards(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["import_eea.py"])
    monkeypatch.setattr(
        importer, "fetch_parquet_urls", lambda country, pollutants: [URL]
    )
    monkeypatch.setattr(
        importer,
        "collect_observations",
        lambda urls, **kwargs: calls.append((urls, kwargs)) or {"sentinel": True},
    )
    monkeypatch.setattr(
        importer,
        "write_observation_snapshot",
        lambda doc, path: calls.append((doc, path.name)),
    )
    importer.main()
    assert calls == [
        ([URL], {"fail_fast": True}),
        ({"sentinel": True}, "observations.json"),
    ]
    for args in (
        ["--registry-manifest", "manifest.json"],
        ["--output-directory", "out"],
        [
            "--check",
            "snapshot.json",
            "--registry-manifest",
            "manifest.json",
            "--output-directory",
            "out",
        ],
    ):
        monkeypatch.setattr(sys, "argv", ["import_eea.py", *args])
        with pytest.raises(SystemExit) as error:
            importer.main()
        assert error.value.code == 2


def test_cli_manifest_error_has_no_traceback_or_production_write(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.import_eea",
            "--registry-manifest",
            str(tmp_path / "missing.json"),
            "--output-directory",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "previous snapshot unchanged" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_real_review_is_reproducible_and_registry_loader_pins_exact_bytes():
    p = NATIONAL / "candidates/2026-11-02"
    with gzip.open(p / "dataflow-d.xml.gz", "rb") as stream:
        body = stream.read(32 * 1024 * 1024 + 1)
    cat, _ = runtime.read_json(p / "catalog-candidate.json")
    inv, _ = runtime.read_json(p / "inventory-candidate.json")
    docs = runtime.prepare_registry(body, cat, inv)
    for name, doc in docs.items():
        assert builder.encode(doc) == (NATIONAL / "registries/v1" / name).read_bytes()
    loaded_cat, loaded_inv, digest = runtime.load_registry(
        NATIONAL / "registries/v1/manifest.json"
    )
    assert loaded_cat == cat and loaded_inv == inv
    assert (
        digest
        == hashlib.sha256(
            (NATIONAL / "registries/v1/manifest.json").read_bytes()
        ).hexdigest()
    )
    assert docs["review.json"]["verifiedSamplingPoints"] == 717
    assert len(docs["review.json"]["missingArcgisCrossChecks"]) == 14
    assert len(docs["review.json"]["stationsWithoutInventoryChannels"]) == 30


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", 2),
        ("catalogVersion", True),
        ("sourceXmlSha256", "0" * 64),
        ("sourceXmlUrl", SOURCE + "wrong"),
        ("reviewBasis", "automatic-discovery"),
    ],
)
def test_inconsistent_review_cannot_be_accepted_by_rehashing(installed, field, value):
    folder, _, _ = installed
    p = folder / "review.json"
    review = json.loads(p.read_bytes())
    review[field] = value
    p.write_bytes(builder.encode(review))
    m = folder / "manifest.json"
    manifest = json.loads(m.read_bytes())
    manifest["files"]["review.json"] = hashlib.sha256(p.read_bytes()).hexdigest()
    m.write_bytes(builder.encode(manifest))
    with pytest.raises(ValueError, match="review"):
        runtime.load_registry(m)


def test_opt_in_cli_success_emits_only_private_bundle(
    installed, tmp_path, monkeypatch, capsys
):
    folder, _, _ = installed
    source = tmp_path / "discovery.txt"
    source.write_bytes(discovery(URL, SECOND))
    snapshot = Path(__file__).resolve().parents[1] / "src/data/observations.json"
    unchanged = snapshot.read_bytes()
    monkeypatch.setattr(importer, "fetch_latest_observation", raw)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_eea.py",
            "--registry-manifest",
            str(folder / "manifest.json"),
            "--output-directory",
            str(tmp_path / "out"),
            "--discovery",
            str(source),
        ],
    )
    importer.main()
    assert json.loads(capsys.readouterr().out)["eligibleChannels"] == 2
    assert snapshot.read_bytes() == unchanged
    assert set(p.name for p in (tmp_path / "out").iterdir()) == {
        "observations.json",
        "collection-report.json",
    }


def test_direct_script_registry_cli_reports_errors_without_package_import_failure(
    tmp_path,
):
    import os

    result = subprocess.run(
        [
            sys.executable,
            "scripts/import_eea.py",
            "--registry-manifest",
            str(tmp_path / "missing.json"),
            "--output-directory",
            str(tmp_path / "out"),
        ],
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Catalog trial failed; previous snapshot unchanged" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_registry_trial_live_discovery_uses_fixed_inventory(
    installed, tmp_path, monkeypatch
):
    folder, _, _ = installed
    calls = []

    def discover(country, pollutants):
        calls.append((country, set(pollutants)))
        return [SECOND, URL]

    monkeypatch.setattr(importer, "fetch_parquet_urls", discover)
    monkeypatch.setattr(importer, "fetch_latest_observation", raw)
    report = runtime.collect_registry_run(folder / "manifest.json", tmp_path / "out")
    assert calls == [("RO", {"SO2", "PM10", "O3", "NO2", "CO", "PM2.5"})]
    assert report["eligibleChannels"] == report["approvedChannels"] == 2
    assert report["discoveryPayloadSha256"] is None
    assert report["channelsWithoutObservation"] == []


@pytest.mark.parametrize("case", ["oversized", "deep", "non_utf8", "missing_catalog"])
def test_registry_cli_malformed_inputs_are_controlled_and_leave_no_output(
    installed, tmp_path, case
):
    folder, _, _ = installed
    manifest = folder / "manifest.json"
    if case == "oversized":
        manifest.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    elif case == "deep":
        manifest.write_bytes(b"[" * 5000 + b"0" + b"]" * 5000)
    elif case == "non_utf8":
        manifest.write_bytes(b"\xff")
    else:
        (folder / "catalog.json").unlink()
    output = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.import_eea",
            "--registry-manifest",
            str(manifest),
            "--output-directory",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Catalog trial failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()
