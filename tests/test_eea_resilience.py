import copy
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import build_eea_candidates as builder
from scripts import check_served_data
from scripts import eea_catalog as catalog_tools
from scripts import eea_registry as registry
from scripts import eea_resilience as resilience
from scripts import import_eea as importer

NOW = "2026-11-10T12:00:00+00:00"
LATER = "2026-11-10T13:00:00+00:00"
LAST = "2026-11-10T14:00:00+00:00"
RECEIPT = {"etag": '"v1"', "payloadSha256": "c" * 64}


def at(hours, minutes=0):
    return (
        datetime(2026, 11, 10, 12, tzinfo=UTC) + timedelta(hours=hours, minutes=minutes)
    ).isoformat()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network operation")

    monkeypatch.setattr(importer, "read_http_response", forbidden)


@pytest.fixture
def bundle(tmp_path):
    root = Path("data/eea/registries/v1")
    catalog = json.loads((root / "catalog.json").read_text())
    inventory = json.loads((root / "inventory.json").read_text())
    selected = []
    for row in inventory["channels"]:
        sid = row["stationId"]
        if (
            sid not in selected
            and sum(c["stationId"] == sid for c in inventory["channels"]) >= 3
        ):
            selected.append(sid)
        if len(selected) == 4:
            break
    catalog["stations"] = [
        row for row in catalog["stations"] if row["stationId"] in selected
    ]
    inventory["channels"] = [
        row
        for sid in selected
        for row in [c for c in inventory["channels"] if c["stationId"] == sid][:3]
    ]
    review = json.loads((root / "review.json").read_text())
    review["verifiedStations"] = len(catalog["stations"])
    review["verifiedSamplingPoints"] = sum(
        len(row["streams"]) for row in inventory["channels"]
    )
    documents = {
        "catalog.json": catalog,
        "inventory.json": inventory,
        "review.json": review,
    }
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"] = {
        name: resilience.digest(builder.encode(doc)) for name, doc in documents.items()
    }
    documents["manifest.json"] = manifest
    folder = tmp_path / "registry"
    builder.write_files(
        {name: builder.encode(doc) for name, doc in documents.items()}, folder
    )
    digest = resilience.digest((folder / "manifest.json").read_bytes())
    return folder, catalog, inventory, digest


def urls_for(inventory):
    return [
        stream["sourceUrl"]
        for row in inventory["channels"]
        for stream in row["streams"]
    ]


def discovery_body(urls):
    return ("ParquetFileUrl\n" + "\n".join(urls) + "\n").encode()


def reading(url, hour=10, value=10.0, validity=1, reported=None):
    _, code, _ = importer.parse_series_url(url)
    start = datetime(2026, 11, 1, hour, tzinfo=UTC).replace(tzinfo=None)
    return {
        "Value": value,
        "AggType": "hour",
        "Start": start,
        "End": start + timedelta(hours=1),
        "ResultTime": start + timedelta(hours=2 if reported is None else reported),
        "Pollutant": int(code),
        "Samplingpoint": "RO/" + Path(url).stem,
        "Unit": "mg.m-3" if int(code) == 10 else "ug.m-3",
        "Validity": validity,
        "Verification": 2,
    }


def table_for(url, validity=1):
    return pa.Table.from_pylist([reading(url, validity=validity)])


def observation(catalog, url, now=NOW, **fields):
    stations = catalog_tools.validate_station_catalog(catalog)
    row = importer.normalize_observation(
        reading(url, **fields), stations[importer.parse_series_url(url)[0]], url
    )
    row["ingestedAt"] = now
    return row


def accepted_bytes(catalog, inventory):
    observations = [
        observation(catalog, url, "2026-11-02T00:00:00+00:00")
        for url in urls_for(inventory)
    ]
    document = {
        "observations": observations,
        "importSummary": {
            "attempted": len(observations),
            "imported": len(observations),
            "skipped": 0,
            "failed": 0,
        },
    }
    return (json.dumps(document, indent=1) + "\n").encode()


class Source:
    def __init__(self, monkeypatch, inventory):
        self.rows = {url: [reading(url)] for url in urls_for(inventory)}
        self.errors = {}
        self.calls = []
        self.etag = '"v1"'
        monkeypatch.setattr(importer, "fetch_parquet_payload", self.fetch)

    def fetch(self, url):
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        sink = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(self.rows[url]), sink)
        return sink.getvalue().to_pybytes(), self.etag


def run(bundle, output, previous=None, seed=None, urls=None, evidence=None, now=NOW):
    folder, _, inventory, _ = bundle
    return registry.collect_controlled_run(
        folder / "manifest.json",
        output,
        initialize=previous is None,
        previous_run=previous,
        previous_snapshot=seed,
        discovery_body=discovery_body(urls_for(inventory) if urls is None else urls),
        authority_evidence=evidence,
        now=now,
    )


def load(bundle, path):
    _, cat, inv, digest = bundle
    state, body, _ = resilience.read_run(path, cat, inv, digest)
    rows = {
        (row["stationId"], row["pollutant"]): row
        for row in (json.loads(body)["observations"] if body else [])
    }
    channels = {(row["stationId"], row["pollutant"]): row for row in state["channels"]}
    return state, channels, rows, body


def key_of(url):
    sid, code, _ = importer.parse_series_url(url)
    return sid, importer.POLLUTANT_NAMES[int(code)]


def seeded(bundle, tmp_path):
    seed = tmp_path / "legacy.json"
    seed.write_bytes(accepted_bytes(bundle[1], bundle[2]))
    return seed


def transition(
    bundle, previous=None, failures=(), empty=(), now=NOW, snapshot=None, body=False
):
    _, cat, inv, digest = bundle
    if previous is None:
        previous = resilience.initial_state(cat, inv, digest, snapshot, now)
    approved = catalog_tools.validate_channel_inventory(inv, cat)
    findings = {
        key: resilience.finding("failed", transient=True)
        if index in failures
        else resilience.finding("empty")
        if index in empty
        else resilience.finding(
            "available",
            candidate=observation(cat, approved[key][0]["sourceUrl"], now),
            receipt=RECEIPT,
        )
        for index, key in enumerate(sorted(approved))
    }
    discovery = catalog_tools.reconcile_discovery(urls_for(inv), cat, inv)
    state, snapshot, publication = resilience.advance_state(
        previous,
        cat,
        inv,
        digest,
        snapshot,
        now,
        "a" * 64 if previous["generation"] else None,
        discovery,
        findings,
    )
    report = resilience.report_for(state, discovery, findings, publication)
    return (state, report, snapshot) if body else (state, report)


@pytest.mark.parametrize(
    "failures,channels,stations,decision",
    [
        ([], 0, 0, "normal"),
        ([0, 1, 3, 4, 6, 7], 6, 3, "normal"),
        ([0, 1, 2, 3, 4, 6, 7], 7, 3, "blocked"),
        ([0, 3, 6, 9], 4, 4, "blocked"),
    ],
)
def test_independent_budget_boundaries(bundle, failures, channels, stations, decision):
    state, report = transition(bundle, failures=failures)
    assert report["budget"] == {
        "degradedChannels": channels,
        "degradedStations": stations,
        "withinBudget": decision == "normal",
    }
    assert report["decision"] == decision
    assert report["blockedBy"] == (None if decision == "normal" else "budgetExceeded")
    assert len(state["channels"]) == 12


def test_losses_accumulate_and_recovery_releases_budget_without_mutating_prior(bundle):
    first, _, body = transition(bundle, failures=[0, 1, 3, 4], body=True)
    before = copy.deepcopy(first)
    second, report, body = transition(
        bundle,
        first,
        failures=[0, 1, 2, 3, 4, 6, 7],
        now=LATER,
        snapshot=body,
        body=True,
    )
    assert report["budget"]["degradedChannels"] == 7
    assert report["decision"] == "blocked"
    assert first == before
    assert second["channels"][0]["degradedSince"] == NOW
    assert second["channels"][2]["degradedSince"] == LATER
    third, report = transition(
        bundle, second, failures=[0, 1, 2, 3, 4, 6], now=LAST, snapshot=body
    )
    assert report["decision"] == "normal"
    assert third["channels"][7]["degradedSince"] is None
    assert third["channels"][7]["reasons"] == []


def test_never_observed_empty_is_distinct_from_persistent_observation_loss(bundle):
    first, report, body = transition(bundle, empty=[0], body=True)
    assert first["channels"][0]["status"] == "healthy-empty"
    assert report["budget"]["degradedChannels"] == 0
    second, _, body = transition(bundle, first, now=LATER, snapshot=body, body=True)
    third, report, body = transition(
        bundle, second, empty=[0], now=LAST, snapshot=body, body=True
    )
    assert third["channels"][0]["hasObserved"] is True
    assert third["channels"][0]["reasons"] == ["unconfirmedObservationLoss"]
    fourth, report = transition(bundle, third, empty=[0], now=LAST, snapshot=body)
    assert fourth["channels"][0]["status"] == "degraded"
    assert report["budget"]["degradedChannels"] == 1


def test_global_discovery_failure_preserves_all_channel_states_and_blocks(bundle):
    _, cat, inv, digest = bundle
    first, _, body = transition(bundle, failures=[0, 1], body=True)
    second, after, publication = resilience.advance_state(
        first, cat, inv, digest, body, LATER, "b" * 64, None, None
    )
    assert second["channels"] == first["channels"]
    assert after == body
    assert publication["decision"] == "blocked"
    assert publication["blockedBy"] == "globalDiscoveryFailure"
    assert second["runIncident"] == "globalDiscoveryFailure"


def test_multiple_causes_count_once_and_missing_fallback_can_keep_valid_preferred(
    bundle,
):
    _, cat, inv, digest = bundle
    inv = copy.deepcopy(inv)
    stream = inv["channels"][0]["streams"][0]
    old_url = stream["sourceUrl"]
    sequence = importer.parse_series_url(old_url)[2]
    new_url = old_url.rsplit("_", 1)[0] + f"_{sequence - 1}.parquet"
    inv["channels"][0]["streams"].append(
        {"sourceUrl": new_url, "samplingPointId": "RO/" + Path(new_url).stem}
    )
    prior = resilience.initial_state(cat, inv, digest, None, NOW)
    discovery = catalog_tools.reconcile_discovery(urls_for(bundle[2]), cat, inv)
    approved = catalog_tools.validate_channel_inventory(inv, cat)
    key = inv["channels"][0]["stationId"], inv["channels"][0]["pollutant"]
    findings = {
        other: resilience.finding(
            "available",
            candidate=observation(cat, approved[other][0]["sourceUrl"]),
            receipt=RECEIPT,
        )
        for other in approved
    }
    healthy = findings[key]
    findings[key] = resilience.finding("failed", transient=True)
    state, _, _ = resilience.advance_state(
        prior, cat, inv, digest, None, NOW, None, discovery, findings
    )
    row = next(
        row for row in state["channels"] if (row["stationId"], row["pollutant"]) == key
    )
    assert row["reasons"] == ["missingRegisteredStream", "sourceFailure"]
    assert resilience.budget(state)["degradedChannels"] == 1
    findings[key] = healthy
    state, body, _ = resilience.advance_state(
        prior, cat, inv, digest, None, NOW, None, discovery, findings
    )
    row = next(
        row for row in state["channels"] if (row["stationId"], row["pollutant"]) == key
    )
    assert row["hasObserved"] is True
    assert row["reasons"] == ["missingRegisteredStream"]
    assert len(json.loads(body)["observations"]) == 12


def test_disk_restart_and_blocked_snapshot_preservation(bundle, tmp_path, monkeypatch):
    folder, cat, inv, digest = bundle
    seed = seeded(bundle, tmp_path)
    snapshot = seed.read_bytes()
    urls = urls_for(inv)
    source = Source(monkeypatch, inv)
    source.errors = {url: TimeoutError("synthetic") for url in urls[:7]}
    first = tmp_path / "run1"
    report = run(bundle, first, seed=seed)
    assert report["decision"] == "blocked"
    assert report["exclusions"] == []
    assert (first / "observations.json").read_bytes() == snapshot
    loaded, body, prior_hash = resilience.read_run(first, cat, inv, digest)
    assert body == snapshot
    assert loaded["generation"] == 1
    source.errors.clear()
    second = tmp_path / "run2"
    report = run(bundle, second, previous=first, now=LATER)
    state, channels, rows, body = load(bundle, second)
    assert report["decision"] == "normal"
    assert state["generation"] == 2
    assert state["previousRunSha256"] == prior_hash
    assert resilience.budget(state)["degradedChannels"] == 0
    assert seed.read_bytes() == (first / "observations.json").read_bytes() == snapshot
    assert {row["ingestedAt"] for row in rows.values()} == {LATER}
    assert all(row["latest"]["confirmedAt"] == LATER for row in channels.values())


def test_unregistered_channel_is_reported_but_never_fetched(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    known = urls_for(inv)
    unknown = known[0].replace(importer.parse_series_url(known[0])[0], "ROZZZZ")
    source = Source(monkeypatch, inv)
    output = tmp_path / "run"
    report = run(bundle, output, urls=known + [unknown])
    assert unknown not in source.calls
    assert report["discovery"]["unregisteredStreams"] == [unknown]
    state, _, rows, _ = load(bundle, output)
    assert len(state["channels"]) == 12
    assert len(rows) == 12


@pytest.mark.parametrize(
    "failure", ["missing", "corrupt", "shrunk", "wrongRegistry", "symlink"]
)
def test_previous_state_errors_never_reset_or_collect(
    bundle, tmp_path, monkeypatch, failure
):
    state, report = transition(bundle, failures=range(12))
    prior = tmp_path / "prior"
    resilience.write_run(prior, state, report, None)
    if failure == "missing":
        (prior / "state.json").unlink()
    elif failure == "corrupt":
        (prior / "state.json").write_bytes(b"invalid")
    elif failure == "symlink":
        (prior / "state.json").rename(tmp_path / "state-copy.json")
        (prior / "state.json").symlink_to(tmp_path / "state-copy.json")
    else:
        state["channels"].pop() if failure == "shrunk" else state.update(
            registryManifestSha256="e" * 64
        )
        (prior / "state.json").write_bytes(builder.encode(state))
        report["stateSha256"] = resilience.digest((prior / "state.json").read_bytes())
        (prior / "collection-report.json").write_bytes(builder.encode(report))

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid state reached collection")

    monkeypatch.setattr(importer, "fetch_parquet_urls", forbidden)
    monkeypatch.setattr(importer, "fetch_parquet_payload", forbidden)
    output = tmp_path / "out"
    with pytest.raises((OSError, ValueError, TypeError, KeyError)):
        run(bundle, output, previous=prior, now=LATER)
    assert not output.exists()


@pytest.mark.parametrize(
    "body",
    [
        b"invalid",
        b"ParquetFileUrl\n",
        b"ParquetFileUrl\nhttps://invalid.example/series\n",
    ],
)
def test_malformed_global_discovery_persists_incident_without_channel_reset(
    bundle, tmp_path, body
):
    folder, cat, inv, digest = bundle
    state, report = transition(bundle, failures=range(12))
    prior = tmp_path / "prior"
    resilience.write_run(prior, state, report, None)
    output = tmp_path / "next"
    result = registry.collect_controlled_run(
        folder / "manifest.json",
        output,
        previous_run=prior,
        discovery_body=body,
        now=LATER,
    )
    restored, _, _ = resilience.read_run(output, cat, inv, digest)
    assert restored["channels"] == state["channels"]
    assert result["decision"] == "blocked"
    assert result["blockedBy"] == "globalDiscoveryFailure"
    assert restored["runIncident"] == "globalDiscoveryFailure"


@pytest.mark.parametrize("fault", ["fileFsync", "rename", "parentFsync"])
def test_bundle_write_faults_preserve_inputs_and_leave_no_exposed_output(
    bundle, tmp_path, monkeypatch, fault
):
    state, report, body = transition(bundle, body=True)
    output = tmp_path / "out"
    if fault == "fileFsync":
        monkeypatch.setattr(
            os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync"))
        )
    elif fault == "rename":
        monkeypatch.setattr(
            os, "rename", lambda *args: (_ for _ in ()).throw(OSError("rename"))
        )
    else:
        real_sync = resilience.sync_directory

        def sync(path):
            if path == output.parent:
                raise OSError("parent fsync")
            return real_sync(path)

        monkeypatch.setattr(resilience, "sync_directory", sync)
    with pytest.raises(OSError):
        resilience.write_run(output, state, report, body)
    assert not output.exists()
    assert not list(tmp_path.glob(".eea-state-*"))


def test_offline_cli_initialization_and_process_restart(bundle, tmp_path):
    folder, cat, inv, digest = bundle
    source = tmp_path / "malformed.txt"
    source.write_bytes(b"invalid")
    seed = seeded(bundle, tmp_path)
    base = [
        sys.executable,
        "-m",
        "scripts.import_eea",
        "--registry-manifest",
        str(folder / "manifest.json"),
        "--controlled-degradation",
        "--discovery",
        str(source),
    ]
    first = tmp_path / "first"
    result = subprocess.run(
        base
        + [
            "--initialize-state",
            "--previous-snapshot",
            str(seed),
            "--output-directory",
            str(first),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["decision"] == "blocked"
    assert summary["blockedBy"] == "globalDiscoveryFailure"
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(quarantine_evidence(bundle, inv["channels"][0]["stationId"]))
    second = tmp_path / "second"
    result = subprocess.run(
        base
        + [
            "--previous-run",
            str(first),
            "--authority-evidence",
            str(evidence),
            "--output-directory",
            str(second),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "corrective"
    state, _, rows, _ = load(bundle, second)
    assert state["generation"] == 2
    assert len(state["channels"]) == 12
    assert len(rows) == 9


@pytest.mark.parametrize("evidence", ["invalid", "unbound", "oversized"])
def test_cli_rejects_unusable_authority_evidence_without_output(
    bundle, tmp_path, evidence
):
    folder = bundle[0]
    source = tmp_path / "evidence.json"
    source.write_bytes(
        {
            "invalid": b"invalid",
            "unbound": b"{}",
            "oversized": b"x" * (catalog_tools.MAX_DOCUMENT_BYTES + 1),
        }[evidence]
    )
    discovery = tmp_path / "discovery.txt"
    discovery.write_bytes(b"invalid")
    output = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.import_eea",
            "--registry-manifest",
            str(folder / "manifest.json"),
            "--controlled-degradation",
            "--initialize-state",
            "--discovery",
            str(discovery),
            "--authority-evidence",
            str(source),
            "--output-directory",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "flags",
    [
        ["--initialize-state"],
        ["--previous-run", "missing"],
        ["--authority-evidence", "missing"],
        ["--controlled-degradation"],
        ["--controlled-degradation", "--initialize-state", "--check", "missing"],
    ],
)
def test_incoherent_state_cli_flags_fail_before_io(flags):
    result = subprocess.run(
        [sys.executable, "scripts/import_eea.py", *flags],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_registry_change_and_backwards_clock_are_rejected(bundle):
    _, cat, inv, digest = bundle
    state, _, body = transition(bundle, body=True)
    changed = copy.deepcopy(inv)
    changed["inventoryVersion"] += 1
    with pytest.raises(ValueError, match="same reviewed registry"):
        resilience.validate_state(state, cat, changed, digest, body)
    with pytest.raises(ValueError, match="backwards"):
        transition(bundle, state, now="2026-11-10T11:00:00+00:00", snapshot=body)


def test_state_hash_binds_snapshot_bytes(bundle, tmp_path):
    _, cat, inv, digest = bundle
    state, report, snapshot = transition(bundle, body=True)
    out = tmp_path / "out"
    resilience.write_run(out, state, report, snapshot)
    (out / "observations.json").write_bytes(snapshot + b"\n")
    with pytest.raises(ValueError, match="snapshot does not match"):
        resilience.read_run(out, cat, inv, digest)
    assert state["acceptedSnapshotSha256"] == hashlib.sha256(snapshot).hexdigest()


@pytest.mark.parametrize(
    "scenario,expected,attempts",
    [
        ("preferredMissing", "missing", []),
        ("hardFailure", "failed", ["high"]),
        ("decodedEmpty", "available", ["high", "low"]),
        ("invalidEmptyIdentity", "failed", ["high"]),
    ],
)
def test_controlled_priority_uses_inventory_before_discovery(
    bundle, monkeypatch, scenario, expected, attempts
):
    _, cat, inv, digest = bundle
    inv = copy.deepcopy(inv)
    inv["channels"] = inv["channels"][:1]
    stream = inv["channels"][0]["streams"][0]
    low = stream["sourceUrl"]
    sequence = importer.parse_series_url(low)[2]
    high = low.rsplit("_", 1)[0] + f"_{sequence + 1}.parquet"
    inv["channels"][0]["streams"].append(
        {"sourceUrl": high, "samplingPointId": "RO/" + Path(high).stem}
    )
    source = Source(monkeypatch, inv)
    source.rows[high] = [reading(high, validity=-1)]
    if scenario == "hardFailure":
        source.errors[high] = TimeoutError("preferred unavailable")
    if scenario == "invalidEmptyIdentity":
        source.rows[high] = [reading(low, validity=-1)]
    result = registry.inspect_channels(
        [low] if scenario == "preferredMissing" else [low, high],
        cat,
        inv,
        resilience.initial_state(cat, inv, digest, None, NOW),
        set(),
        NOW,
    )
    assert [finding["outcome"] for finding in result.values()] == [expected]
    assert ["high" if url == high else "low" for url in source.calls] == attempts
    if scenario == "hardFailure":
        assert next(iter(result.values()))["transient"] is True
    if scenario == "invalidEmptyIdentity":
        assert next(iter(result.values()))["transient"] is False


def test_discovery_omissions_accumulate_across_saved_runs_without_retirement(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first, urls=urls[4:])
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, urls=urls[7:], now=LATER)
    state, _, _, _ = load(bundle, second)
    assert report["budget"]["degradedChannels"] == 7
    assert report["decision"] == "blocked"
    assert len(state["channels"]) == 12
    assert all("retired" not in row for row in state["channels"])


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicateKey",
        "duplicateChannel",
        "wrongStatus",
        "boolGeneration",
        "futureSince",
        "badBudgetType",
        "unknownField",
        "inventedConfirmation",
        "foreignVersion",
        "changedRecord",
        "falseNormal",
        "silentExclusion",
        "revivedWithdrawal",
        "inventedFallback",
        "hiddenOmission",
        "foreignEnvelope",
    ],
)
def test_strict_state_and_report_validation(bundle, tmp_path, corruption):
    _, cat, inv, digest = bundle
    state, report, body = transition(bundle, failures=[0], body=True)
    if corruption == "duplicateChannel":
        state["channels"][-1] = copy.deepcopy(state["channels"][0])
    elif corruption == "wrongStatus":
        state["channels"][0]["status"] = "retired"
    elif corruption == "boolGeneration":
        state["generation"] = True
    elif corruption == "futureSince":
        state["channels"][0]["degradedSince"] = LATER
    elif corruption == "unknownField":
        state["resetBudget"] = True
    elif corruption == "inventedConfirmation":
        state["channels"][1]["latest"]["confirmedAt"] = LATER
    elif corruption == "foreignVersion":
        state["channels"][1]["latest"]["sourceUrl"] = state["channels"][2]["latest"][
            "sourceUrl"
        ]
    elif corruption == "changedRecord":
        state["channels"][1]["latest"]["recordSha256"] = "0" * 64
    elif corruption == "falseNormal":
        state["channels"][1].update(
            status="degraded", reasons=["invalidSource"], degradedSince=NOW
        )
        for index in range(2, 8):
            state["channels"][index].update(
                status="degraded", reasons=["invalidSource"], degradedSince=NOW
            )
        report["budget"] = resilience.budget(state)
    elif corruption == "silentExclusion":
        report.update(decision="corrective", blockedBy="budgetExceeded")
    elif corruption == "revivedWithdrawal":
        latest = state["channels"][1]["latest"]
        state["channels"][1]["withdrawals"] = [
            {
                **{key: latest[key] for key in resilience.VERSION},
                "confirmedAt": NOW,
                "etag": None,
                "payloadSha256": "c" * 64,
            }
        ]
    elif corruption == "inventedFallback":
        report["retainedFallbacks"] = [
            [state["channels"][0]["stationId"], state["channels"][0]["pollutant"]]
        ]
    elif corruption == "hiddenOmission":
        report["omittedReadings"] = [
            [state["channels"][1]["stationId"], state["channels"][1]["pollutant"]]
        ]
    elif corruption == "foreignEnvelope":
        document = json.loads(body)
        document["publication"]["degradedChannels"] = 0
        body = resilience.encode_snapshot(document)
        state["acceptedSnapshotSha256"] = resilience.digest(body)
    if corruption == "badBudgetType":
        report["budget"]["degradedChannels"] = 1.0
    out = tmp_path / "out"
    resilience.write_run(out, state, report, body)
    raw = (out / "state.json").read_bytes()
    if corruption == "duplicateKey":
        raw = raw.replace(b'"generation": 1', b'"generation": 1, "generation": 2')
        (out / "state.json").write_bytes(raw)
    report["stateSha256"] = resilience.digest(raw)
    (out / "collection-report.json").write_bytes(builder.encode(report))
    with pytest.raises((ValueError, TypeError, KeyError)):
        resilience.read_run(out, cat, inv, digest)


def test_existing_output_and_symlink_are_never_overwritten(bundle, tmp_path):
    state, report, body = transition(bundle, body=True)
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "owner.txt"
    sentinel.write_bytes(b"owner content")
    link = tmp_path / "link"
    link.symlink_to(existing, target_is_directory=True)
    for output in (existing, link):
        with pytest.raises(ValueError, match="new output"):
            resilience.write_run(output, state, report, body)
    assert sentinel.read_bytes() == b"owner content"


def test_missing_outcome_cannot_silently_keep_previous_healthy_channel(bundle):
    _, cat, inv, digest = bundle
    state, _, body = transition(bundle, body=True)
    discovery = catalog_tools.reconcile_discovery(urls_for(inv), cat, inv)
    findings = {
        key: resilience.finding("empty")
        for key in catalog_tools.validate_channel_inventory(inv, cat)
    }
    findings.pop(next(iter(findings)))
    with pytest.raises(ValueError, match="exactly one outcome"):
        resilience.advance_state(
            state, cat, inv, digest, body, LATER, "a" * 64, discovery, findings
        )


def test_real_v1_inventory_state_roundtrip_is_offline_and_bounded(tmp_path):
    cat, inv, manifest_digest = registry.load_registry(
        Path("data/eea/registries/v1/manifest.json")
    )
    snapshot = Path("src/data/observations.json").read_bytes()
    initial = resilience.initial_state(cat, inv, manifest_digest, snapshot, NOW)
    state, body, publication = resilience.advance_state(
        initial, cat, inv, manifest_digest, snapshot, NOW, None, None, None
    )
    report = resilience.report_for(state, None, None, publication)
    output = tmp_path / "v1-state"
    resilience.write_run(output, state, report, body)
    restored, raw, _ = resilience.read_run(output, cat, inv, manifest_digest)
    assert len(restored["channels"]) == 717
    assert raw == snapshot
    assert report["decision"] == "blocked"
    assert sum(row["latest"] is not None for row in restored["channels"]) == 640
    assert all(
        row["latest"] is None or row["latest"]["confirmedAt"] is None
        for row in restored["channels"]
    )
    assert len((output / "state.json").read_bytes()) < catalog_tools.MAX_DOCUMENT_BYTES


def test_confirmation_binds_exact_verified_source_version(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    seed = seeded(bundle, tmp_path)
    source = Source(monkeypatch, inv)
    source.errors = {url: TimeoutError("synthetic") for url in urls[:7]}
    source.rows[urls[8]] = [reading(urls[8], hour=11)]
    source.rows[urls[9]] = [reading(urls[9], validity=-99), reading(urls[9], hour=9)]
    source.rows[urls[10]] = [reading(urls[10], value=11.0)]
    first = tmp_path / "first"
    report = run(bundle, first, seed=seed)
    _, channels, _, body = load(bundle, first)
    assert report["decision"] == "blocked"
    assert body == seed.read_bytes()
    exact = channels[key_of(urls[7])]["latest"]
    assert exact["confirmedAt"] == NOW
    assert exact["etag"] == '"v1"'
    assert len(exact["payloadSha256"]) == 64
    for url in urls[:7] + urls[8:11]:
        assert channels[key_of(url)]["latest"]["confirmedAt"] is None
        assert channels[key_of(url)]["latest"]["payloadSha256"] is None
    assert channels[key_of(urls[9])]["reasons"] == ["unconfirmedRegression"]
    assert channels[key_of(urls[10])]["reasons"] == ["versionConflict"]
    assert channels[key_of(urls[8])]["status"] == "available"


def test_legacy_readings_get_no_invented_fallback_window(bundle, tmp_path, monkeypatch):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    seed = seeded(bundle, tmp_path)
    source = Source(monkeypatch, inv)
    source.errors = {urls[0]: TimeoutError("synthetic")}
    output = tmp_path / "run"
    report = run(bundle, output, seed=seed, urls=urls[:1] + urls[2:])
    _, channels, rows, _ = load(bundle, output)
    assert report["decision"] == "normal"
    assert report["retainedFallbacks"] == []
    assert report["omittedReadings"] == [list(key_of(url)) for url in urls[:2]]
    assert key_of(urls[0]) not in rows and key_of(urls[1]) not in rows
    assert channels[key_of(urls[0])]["reasons"] == ["sourceFailure"]
    assert channels[key_of(urls[1])]["reasons"] == ["missingRegisteredStream"]
    assert channels[key_of(urls[0])]["latest"]["confirmedAt"] is None
    assert json.loads((output / "observations.json").read_bytes())["publication"] == {
        "schemaVersion": 1,
        "mode": "normal",
        "generation": 1,
        "generatedAt": NOW,
        "degradedChannels": 2,
        "degradedStations": 1,
    }


@pytest.mark.parametrize("cause", ["transport", "http503", "discovery"])
def test_transport_fallback_ends_at_24_hours_without_retimestamp(
    bundle, tmp_path, monkeypatch, cause
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    key = key_of(urls[0])
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    original = load(bundle, first)[2][key]
    if cause == "transport":
        source.errors[urls[0]] = urllib.error.URLError("synthetic")
    elif cause == "http503":
        source.errors[urls[0]] = urllib.error.HTTPError(urls[0], 503, "x", {}, None)
    listed = urls[1:] if cause == "discovery" else urls
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, urls=listed, now=at(23, 59))
    _, channels, rows, _ = load(bundle, second)
    assert report["decision"] == "normal"
    assert report["retainedFallbacks"] == [list(key)]
    assert rows[key] == original
    assert rows[key]["ingestedAt"] == NOW
    assert channels[key]["status"] == "degraded"
    assert channels[key]["latest"]["confirmedAt"] == NOW
    third = tmp_path / "third"
    report = run(bundle, third, previous=second, urls=listed, now=at(24))
    _, channels, rows, _ = load(bundle, third)
    assert report["decision"] == "normal"
    assert report["omittedReadings"] == [list(key)]
    assert key not in rows and len(rows) == 11
    assert channels[key]["status"] == "degraded"
    assert channels[key]["degradedSince"] == at(23, 59)
    assert channels[key]["latest"]["confirmedAt"] == NOW
    source.errors.clear()
    fourth = tmp_path / "fourth"
    run(bundle, fourth, previous=third, now=at(25))
    _, channels, rows, _ = load(bundle, fourth)
    assert rows[key]["ingestedAt"] == at(25)
    assert channels[key]["status"] == "available"


@pytest.mark.parametrize("cause", ["identity", "http404", "schema", "unknownValidity"])
def test_hard_source_failures_never_use_transport_fallback(
    bundle, tmp_path, monkeypatch, cause
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    key = key_of(urls[0])
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    if cause == "identity":
        source.rows[urls[0]] = [reading(urls[1])]
    elif cause == "http404":
        source.errors[urls[0]] = urllib.error.HTTPError(urls[0], 404, "x", {}, None)
    elif cause == "schema":
        source.rows[urls[0]] = [{"Value": 1.0}]
    else:
        source.rows[urls[0]] = [reading(urls[0]), reading(urls[0], 9, validity=7)]
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=LATER)
    _, channels, rows, _ = load(bundle, second)
    assert report["retainedFallbacks"] == []
    assert key not in rows
    assert channels[key]["reasons"] == ["invalidSource"]
    assert channels[key]["latest"]["confirmedAt"] == NOW


def test_withdrawal_is_recorded_and_stale_versions_cannot_revive_it(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    url = urls[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    source.rows[url] = [reading(url, 9), reading(url, 10)]
    first = tmp_path / "first"
    run(bundle, first)
    source.rows[url] = [reading(url, 9), reading(url, 10, validity=-1, reported=5)]
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=LATER)
    _, channels, rows, _ = load(bundle, second)
    assert report["decision"] == "normal"
    assert report["exclusions"] == [
        {"stationId": key[0], "pollutant": key[1], "reason": "withdrawn"}
    ]
    assert rows[key]["observedTo"] == "2026-11-01T10:00:00+01:00"
    withdrawal = channels[key]["withdrawals"][0]
    assert withdrawal["observedTo"] == "2026-11-01T11:00:00+01:00"
    assert withdrawal["reportedAt"] == "2026-11-01T15:00:00+01:00"
    assert withdrawal["confirmedAt"] == LATER
    assert channels[key]["status"] == "available"
    for index, (reported, reason) in enumerate(
        [(2, "staleSourceVersion"), (5, "versionConflict")]
    ):
        source.rows[url] = [reading(url, 9), reading(url, 10, reported=reported)]
        output = tmp_path / f"stale{index}"
        run(bundle, output, previous=second, now=LAST)
        _, channels, rows, _ = load(bundle, output)
        assert channels[key]["reasons"] == [reason]
        assert key not in rows
        assert channels[key]["latest"]["observedTo"] == "2026-11-01T10:00:00+01:00"
        assert len(channels[key]["withdrawals"]) == 1
    source.rows[url] = [reading(url, 9), reading(url, 10, reported=6)]
    output = tmp_path / "revalidated"
    run(bundle, output, previous=second, now=LAST)
    _, channels, rows, _ = load(bundle, output)
    assert rows[key]["observedTo"] == "2026-11-01T11:00:00+01:00"
    assert channels[key]["status"] == "available"


def test_withdrawal_without_replacement_stays_degraded(bundle, tmp_path, monkeypatch):
    _, _, inv, _ = bundle
    url = urls_for(inv)[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    source.rows[url] = [reading(url, validity=-1)]
    previous = first
    for index, now in enumerate((LATER, LAST)):
        output = tmp_path / f"next{index}"
        run(bundle, output, previous=previous, now=now)
        _, channels, rows, _ = load(bundle, output)
        assert key not in rows
        assert channels[key]["latest"] is None
        assert channels[key]["reasons"] == ["observationWithdrawn"]
        assert channels[key]["degradedSince"] == LATER
        assert len(channels[key]["withdrawals"]) == 1
        previous = output


@pytest.mark.parametrize(
    "rows,reasons,kept",
    [
        ("absentOlder", ["unconfirmedRegression"], False),
        ("maintenanceOlder", ["unconfirmedRegression"], False),
        ("olderReport", ["staleSourceVersion"], False),
        ("changedValue", ["versionConflict"], False),
        ("correction", [], True),
        ("newerInterval", [], True),
        ("staleButNewerInterval", [], True),
        ("duplicateInterval", ["invalidSource"], False),
        ("empty", ["unconfirmedObservationLoss"], False),
    ],
)
def test_version_order_never_comes_from_arrival_or_payload_hash(
    bundle, tmp_path, monkeypatch, rows, reasons, kept
):
    _, _, inv, _ = bundle
    url = urls_for(inv)[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    source.etag = '"v2"'
    source.rows[url] = {
        "absentOlder": [reading(url, 9)],
        "maintenanceOlder": [reading(url, 9), reading(url, validity=-99)],
        "olderReport": [reading(url, reported=1)],
        "changedValue": [reading(url, value=11.0)],
        "correction": [reading(url, value=11.0, reported=3)],
        "newerInterval": [reading(url, value=11.0), reading(url, 11)],
        "staleButNewerInterval": [reading(url, reported=1), reading(url, 11)],
        "duplicateInterval": [reading(url), reading(url)],
        "empty": [reading(url, 9, validity=-99)],
    }[rows]
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=LATER)
    _, channels, published, _ = load(bundle, second)
    assert report["decision"] == "normal"
    assert channels[key]["reasons"] == reasons
    assert (key in published) is kept
    assert channels[key]["withdrawals"] == []
    if kept:
        assert channels[key]["latest"]["etag"] == '"v2"'
        assert channels[key]["latest"]["confirmedAt"] == LATER
    else:
        assert channels[key]["latest"]["etag"] == '"v1"'
        assert channels[key]["latest"]["confirmedAt"] == NOW
        assert channels[key]["latest"]["observedTo"] == "2026-11-01T11:00:00+01:00"


def two_streams(bundle):
    folder, cat, inv, _ = bundle
    low = inv["channels"][0]["streams"][0]["sourceUrl"]
    sequence = importer.parse_series_url(low)[2]
    high = low.rsplit("_", 1)[0] + f"_{sequence + 1}.parquet"
    inv["channels"][0]["streams"].append(
        {"sourceUrl": high, "samplingPointId": "RO/" + Path(high).stem}
    )
    documents = {
        name: json.loads((folder / name).read_text())
        for name in ("catalog.json", "review.json", "manifest.json")
    }
    documents["inventory.json"] = inv
    documents["review.json"]["verifiedSamplingPoints"] += 1
    documents["manifest.json"]["files"] = {
        name: resilience.digest(builder.encode(documents[name]))
        for name in ("catalog.json", "inventory.json", "review.json")
    }
    changed = folder.parent / "two-streams"
    builder.write_files(
        {name: builder.encode(doc) for name, doc in documents.items()}, changed
    )
    bundle = (
        changed,
        cat,
        inv,
        resilience.digest((changed / "manifest.json").read_bytes()),
    )
    return bundle, low, high


def test_lower_sequence_cannot_introduce_an_unconfirmed_regression(
    bundle, tmp_path, monkeypatch
):
    bundle, low, high = two_streams(bundle)
    inv = bundle[2]
    key = key_of(low)
    source = Source(monkeypatch, inv)
    source.rows[low] = [reading(low, 9)]
    first = tmp_path / "first"
    run(bundle, first)
    assert load(bundle, first)[2][key]["sourceUrl"] == high
    source.rows[high] = [reading(high, validity=-99)]
    second = tmp_path / "second"
    run(bundle, second, previous=first, now=LATER)
    _, channels, rows, _ = load(bundle, second)
    assert channels[key]["reasons"] == ["unconfirmedRegression"]
    assert key not in rows
    source.rows[high] = [reading(high, validity=-1)]
    third = tmp_path / "third"
    run(bundle, third, previous=second, now=LAST)
    _, channels, rows, _ = load(bundle, third)
    assert rows[key]["sourceUrl"] == low
    assert channels[key]["withdrawals"][0]["sourceUrl"] == high
    assert channels[key]["status"] == "available"


def quarantine_evidence(bundle, station, status="quarantined", **changes):
    finding = {
        "stationId": station,
        "kind": "geometry",
        "status": status,
        "sourceUrl": "https://cdr.eionet.europa.eu/ro/eu/aqd/d/envtest/review.xml",
        "reportingYear": 2026,
        "xmlSha256": "d" * 64,
        "headerId": "RO-D-2026-review",
        "reviewedAt": "2026-11-09T00:00:00+00:00",
    }
    finding.update(changes)
    return builder.encode(
        {
            "schemaVersion": 1,
            "registryManifestSha256": changes.pop("manifest", bundle[3]),
            "findings": [
                {k: v for k, v in finding.items() if k != "manifest"},
            ],
        }
    )


def test_quarantine_stops_readings_counts_channels_and_needs_reviewed_resolution(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    station = key_of(urls[0])[0]
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    source.calls.clear()
    second = tmp_path / "second"
    report = run(
        bundle,
        second,
        previous=first,
        evidence=quarantine_evidence(bundle, station),
        now=LATER,
    )
    state, channels, rows, _ = load(bundle, second)
    assert report["decision"] == "normal"
    assert report["budget"]["degradedChannels"] == 3
    assert report["budget"]["degradedStations"] == 1
    assert report["outcomes"]["quarantined"] == 3
    assert {row["reason"] for row in report["exclusions"]} == {"quarantined"}
    assert len(report["exclusions"]) == 3
    assert not any(key_of(url)[0] == station for url in source.calls)
    assert all(key[0] != station for key in rows) and len(rows) == 9
    assert state["quarantine"][0]["since"] == LATER
    assert state["quarantine"][0]["xmlSha256"] == "d" * 64
    for url in urls[:3]:
        assert channels[key_of(url)]["reasons"] == ["quarantined"]
        assert channels[key_of(url)]["latest"]["confirmedAt"] == NOW
    third = tmp_path / "third"
    run(bundle, third, previous=second, now=LAST)
    state, channels, rows, _ = load(bundle, third)
    assert state["quarantine"][0]["since"] == LATER
    assert len(rows) == 9
    fourth = tmp_path / "fourth"
    run(
        bundle,
        fourth,
        previous=third,
        evidence=quarantine_evidence(bundle, station, "resolved"),
        now=at(3),
    )
    state, channels, rows, _ = load(bundle, fourth)
    assert state["quarantine"] == []
    assert len(rows) == 12
    assert channels[key_of(urls[0])]["status"] == "available"


@pytest.mark.parametrize(
    "change",
    [
        {"manifest": "e" * 64},
        {"xmlSha256": None},
        {"status": "resolved"},
        {"kind": "rename"},
        {"stationId": "ROZZZZ"},
        {"sourceUrl": "https://example.com/review.xml"},
        {"reviewedAt": "2026-11-11T00:00:00+00:00"},
    ],
)
def test_unreviewed_or_unbound_authority_evidence_is_rejected(
    bundle, tmp_path, monkeypatch, change
):
    _, cat, inv, _ = bundle
    station = inv["channels"][0]["stationId"]
    if change.get("xmlSha256", "") is None:
        change = {
            "xmlSha256": catalog_tools.validate_station_catalog(cat)[station][
                "metadataSourceSha256"
            ]
        }
    source = Source(monkeypatch, inv)
    output = tmp_path / "out"
    with pytest.raises((ValueError, TypeError, KeyError)):
        run(bundle, output, evidence=quarantine_evidence(bundle, station, **change))
    assert not output.exists()
    assert source.calls == []


def test_blocked_batch_allows_only_strictly_corrective_exclusions(
    bundle, tmp_path, monkeypatch
):
    _, cat, inv, digest = bundle
    urls = urls_for(inv)
    source = Source(monkeypatch, inv)
    for url in urls:
        source.rows[url] = [reading(url, 9), reading(url, 10)]
    first = tmp_path / "first"
    run(bundle, first)
    _, _, before, _ = load(bundle, first)
    station = key_of(urls[9])[0]
    withdrawn = key_of(urls[7])
    source.errors = {url: TimeoutError("synthetic") for url in urls[:7]}
    source.rows[urls[7]] = [reading(urls[7], 9), reading(urls[7], 10, validity=-1)]
    source.rows[urls[8]] = [reading(urls[8], 10), reading(urls[8], 11)]
    second = tmp_path / "second"
    report = run(
        bundle,
        second,
        previous=first,
        evidence=quarantine_evidence(bundle, station),
        now=LATER,
    )
    state, channels, rows, body = load(bundle, second)
    assert report["decision"] == "corrective"
    assert report["blockedBy"] == "budgetExceeded"
    assert report["budget"]["withinBudget"] is False
    assert report["retainedFallbacks"] == report["omittedReadings"] == []
    excluded = {
        (row["stationId"], row["pollutant"]): row["reason"]
        for row in report["exclusions"]
    }
    assert excluded == {
        withdrawn: "withdrawn",
        **{key_of(url): "quarantined" for url in urls[9:]},
    }
    assert rows == {key: row for key, row in before.items() if key not in excluded}
    assert list(rows) == [key for key in before if key not in excluded]
    assert all(row["ingestedAt"] == NOW for row in rows.values())
    document = json.loads(body)
    assert document["publication"]["mode"] == "corrective"
    assert document["publication"]["generatedAt"] == LATER
    assert document["publication"]["degradedChannels"] == 10
    assert document["importSummary"] == {
        "attempted": 12,
        "imported": 8,
        "skipped": 4,
        "failed": 0,
    }
    assert channels[withdrawn]["latest"] is None
    assert channels[withdrawn]["withdrawals"][0]["confirmedAt"] == LATER
    assert channels[withdrawn]["hasObserved"] is True
    unpublished = channels[key_of(urls[8])]
    assert unpublished["latest"]["observedTo"] == "2026-11-01T11:00:00+01:00"
    assert unpublished["latest"]["confirmedAt"] == NOW
    assert channels[key_of(urls[0])]["degradedSince"] == LATER
    source.errors.clear()
    third = tmp_path / "third"
    report = run(bundle, third, previous=second, now=LAST)
    _, channels, rows, _ = load(bundle, third)
    assert report["decision"] == "normal"
    assert rows[withdrawn]["observedTo"] == "2026-11-01T10:00:00+01:00"
    assert rows[key_of(urls[8])]["observedTo"] == "2026-11-01T12:00:00+01:00"
    assert all(key[0] != station for key in rows)
    assert report["budget"]["degradedChannels"] == 3


def test_blocked_batch_without_confirmed_exclusion_keeps_exact_snapshot(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    urls = urls_for(inv)
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    accepted = (first / "observations.json").read_bytes()
    source.errors = {url: TimeoutError("synthetic") for url in urls[:7]}
    source.rows[urls[7]] = [reading(urls[7], 11)]
    source.rows[urls[8]] = [reading(urls[8], 9)]
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=at(30))
    _, channels, _, body = load(bundle, second)
    assert report["decision"] == "blocked"
    assert report["exclusions"] == []
    assert body == accepted
    assert channels[key_of(urls[0])]["latest"]["confirmedAt"] == NOW
    assert channels[key_of(urls[7])]["latest"]["observedTo"].startswith("2026-11-01T11")
    assert channels[key_of(urls[8])]["reasons"] == ["unconfirmedRegression"]


def test_corrective_publication_may_empty_the_snapshot_but_normal_cannot(
    bundle, tmp_path, monkeypatch
):
    _, cat, inv, digest = bundle
    urls = urls_for(inv)
    source = Source(monkeypatch, inv)
    for url in urls[1:]:
        source.rows[url] = [reading(url, validity=-1)]
    first = tmp_path / "first"
    assert run(bundle, first)["decision"] == "normal"
    assert len(load(bundle, first)[2]) == 1
    source.rows[urls[0]] = [reading(urls[0], validity=-1)]
    source.errors = {url: TimeoutError("synthetic") for url in urls[1:8]}
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=LATER)
    _, channels, rows, body = load(bundle, second)
    document = json.loads(body)
    assert report["decision"] == "corrective"
    assert rows == {} and document["observations"] == []
    assert document["publication"]["mode"] == "corrective"
    importer.validate_document(document)
    result, degraded = check_served_data.assess_snapshot(
        document, datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
    )
    assert degraded is True
    assert result["latest_measurement"] is None
    assert result["latest_ingestion"] == LATER
    assert result["ingestion_is_recent"] is True
    assert result["publication_mode"] == "corrective"
    target = tmp_path / "legacy.json"
    with pytest.raises(ValueError):
        importer.write_observation_snapshot(document, target)
    assert not target.exists()
    source.errors.clear()
    third = tmp_path / "third"
    report = run(bundle, third, previous=second, now=LAST)
    assert report["decision"] == "blocked"
    assert report["blockedBy"] == "emptyNormalBatch"
    assert (third / "observations.json").read_bytes() == body
    for change in (
        {"mode": "normal"},
        {"mode": "repair"},
        {"generation": 0},
        {"generatedAt": "2026-11-10T13:00:00"},
        {"extra": 1},
    ):
        broken = copy.deepcopy(document)
        broken["publication"].update(change)
        with pytest.raises((ValueError, TypeError)):
            importer.validate_document(broken)
    normal = json.loads((first / "observations.json").read_bytes())
    normal["publication"]["generatedAt"] = "2026-11-10T11:59:59+00:00"
    with pytest.raises(ValueError, match="precedes"):
        importer.validate_document(normal)


def hour_end(hour):
    return f"2026-11-01T{hour + 1:02d}:00:00+01:00"


def test_rolling_withdrawals_retire_old_evidence_without_freezing_or_revival(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    url = urls_for(inv)[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    source.rows[url] = [reading(url, 0)]
    previous = tmp_path / "run0"
    run(bundle, previous)
    limit = resilience.MAX_WITHDRAWALS
    for hour in range(1, limit + 2):
        source.rows[url] = [
            *(reading(url, h, validity=-1, reported=5) for h in range(hour)),
            reading(url, hour),
        ]
        output = tmp_path / f"run{hour}"
        report = run(bundle, output, previous=previous, now=at(hour))
        _, channels, rows, _ = load(bundle, output)
        assert report["decision"] == "normal"
        assert rows[key]["observedTo"] == hour_end(hour)
        assert channels[key]["status"] == "available"
        assert len(channels[key]["withdrawals"]) == min(hour, limit)
        previous = output
    assert channels[key]["retiredBefore"] == {url: hour_end(0)}
    assert [w["observedTo"] for w in channels[key]["withdrawals"]] == [
        hour_end(h) for h in range(1, limit + 1)
    ]
    last = limit + 1
    withdrawn = [reading(url, h, validity=-1, reported=5) for h in range(1, last + 1)]
    for index, (rows_now, reason) in enumerate(
        [
            ([reading(url, 0), *withdrawn], "retiredInterval"),
            ([reading(url, 0, reported=9), *withdrawn], "retiredInterval"),
            (
                [reading(url, 0), reading(url, 1), *withdrawn[1:]],
                "staleSourceVersion",
            ),
        ]
    ):
        source.rows[url] = rows_now
        output = tmp_path / f"revival{index}"
        run(bundle, output, previous=previous, now=at(last + 1))
        state, channels, rows, _ = load(bundle, output)
        assert key not in rows
        assert channels[key]["latest"] is None
        assert channels[key]["reasons"] == [reason]
        assert len(channels[key]["withdrawals"]) == limit
        again = tmp_path / f"revival-again{index}"
        run(bundle, again, previous=output, now=at(last + 2))
        _, channels, rows, _ = load(bundle, again)
        assert key not in rows and channels[key]["latest"] is None
    source.rows[url] = [*withdrawn, reading(url, last + 1)]
    output = tmp_path / "recovered"
    run(bundle, output, previous=again, now=at(last + 3))
    _, channels, rows, _ = load(bundle, output)
    assert rows[key]["observedTo"] == hour_end(last + 1)
    assert channels[key]["status"] == "available"


def test_descending_withdrawals_stop_at_the_retired_floor(
    bundle, tmp_path, monkeypatch
):
    _, _, inv, _ = bundle
    url = urls_for(inv)[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    top = resilience.MAX_WITHDRAWALS + 2
    source.rows[url] = [reading(url, h) for h in range(top + 1)]
    previous = tmp_path / "run0"
    run(bundle, previous)
    for step in range(1, resilience.MAX_WITHDRAWALS + 1):
        lowest = top - step + 1
        source.rows[url] = [
            reading(url, h, validity=-1 if h >= lowest else 1, reported=5)
            if h >= lowest
            else reading(url, h)
            for h in range(top + 1)
        ]
        output = tmp_path / f"run{step}"
        run(bundle, output, previous=previous, now=at(step))
        _, channels, rows, _ = load(bundle, output)
        assert rows[key]["observedTo"] == hour_end(lowest - 1)
        assert channels[key]["retiredBefore"] == {}
        previous = output
    source.rows[url] = [
        reading(url, h, validity=-1, reported=5) if h >= 2 else reading(url, h)
        for h in range(top + 1)
    ]
    for index in range(2):
        output = tmp_path / f"floor{index}"
        report = run(bundle, output, previous=previous, now=at(top + index))
        _, channels, rows, _ = load(bundle, output)
        assert report["decision"] == "normal"
        assert key not in rows
        assert channels[key]["latest"] is None
        assert channels[key]["reasons"] == ["retiredInterval"]
        assert channels[key]["retiredBefore"] == {url: hour_end(2)}
        assert len(channels[key]["withdrawals"]) == resilience.MAX_WITHDRAWALS
        previous = output


def test_retired_floor_belongs_to_its_native_stream(bundle, tmp_path, monkeypatch):
    bundle, low, high = two_streams(bundle)
    _, cat, inv, digest = bundle
    key = key_of(low)
    source = Source(monkeypatch, inv)
    source.rows[low] = [reading(low, 9)]
    first = tmp_path / "first"
    run(bundle, first)
    state, body, _ = resilience.read_run(first, cat, inv, digest)
    assert state["channels"][0]["latest"]["sourceUrl"] == high
    state["channels"][0]["retiredBefore"] = {high: hour_end(9)}
    report = json.loads((first / "collection-report.json").read_bytes())
    report["stateSha256"] = resilience.digest(builder.encode(state))
    floored = tmp_path / "floored"
    resilience.write_run(floored, state, report, body)
    source.rows[high] = [reading(high, validity=-1)]
    second = tmp_path / "second"
    run(bundle, second, previous=floored, now=LATER)
    _, channels, rows, _ = load(bundle, second)
    assert rows[key]["sourceUrl"] == low
    assert rows[key]["observedTo"] == hour_end(9)
    assert channels[key]["status"] == "available"
    assert channels[key]["retiredBefore"] == {high: hour_end(9)}


def test_retired_reading_on_preferred_stream_never_permits_a_lower_sequence(
    bundle, tmp_path, monkeypatch
):
    bundle, low, high = two_streams(bundle)
    _, cat, inv, digest = bundle
    key = key_of(low)
    source = Source(monkeypatch, inv)
    source.rows[high] = [reading(high, 8), reading(high, 10)]
    source.rows[low] = [reading(low, 9)]
    first = tmp_path / "first"
    run(bundle, first)
    state, body, _ = resilience.read_run(first, cat, inv, digest)
    state["channels"][0]["retiredBefore"] = {high: hour_end(8)}
    report = json.loads((first / "collection-report.json").read_bytes())
    report["stateSha256"] = resilience.digest(builder.encode(state))
    floored = tmp_path / "floored"
    resilience.write_run(floored, state, report, body)
    source.rows[high] = [reading(high, 8), reading(high, validity=-1, reported=5)]
    second = tmp_path / "second"
    run(bundle, second, previous=floored, now=LATER)
    _, channels, rows, _ = load(bundle, second)
    assert key not in rows
    assert channels[key]["latest"] is None
    assert channels[key]["reasons"] == ["retiredInterval"]
    assert low not in source.calls[-len(urls_for(inv)) :]


def test_ninth_withdrawal_in_a_corrective_run_keeps_a_valid_state(
    bundle, tmp_path, monkeypatch
):
    _, cat, inv, digest = bundle
    urls = urls_for(inv)
    url = urls[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    previous = tmp_path / "run0"
    source.rows[url] = [reading(url, 0)]
    run(bundle, previous)
    limit = resilience.MAX_WITHDRAWALS
    for hour in range(1, limit + 1):
        source.rows[url] = [
            *(reading(url, h, validity=-1, reported=5) for h in range(hour)),
            reading(url, hour),
        ]
        output = tmp_path / f"run{hour}"
        run(bundle, output, previous=previous, now=at(hour))
        previous = output
    source.rows[url] = [
        *(reading(url, h, validity=-1, reported=5) for h in range(limit + 1)),
        reading(url, limit + 1),
    ]
    source.errors = {u: TimeoutError("synthetic") for u in urls[1:8]}
    corrective = tmp_path / "corrective"
    report = run(bundle, corrective, previous=previous, now=at(limit + 1))
    state, channels, rows, _ = load(bundle, corrective)
    assert report["decision"] == "corrective"
    assert key not in rows and channels[key]["latest"] is None
    assert len(channels[key]["withdrawals"]) == limit
    assert channels[key]["retiredBefore"] == {url: hour_end(0)}
    source.errors.clear()
    source.rows[url] = [reading(url, 0, reported=9)]
    after = tmp_path / "after"
    run(bundle, after, previous=corrective, now=at(limit + 2))
    _, channels, rows, _ = load(bundle, after)
    assert key not in rows
    assert channels[key]["reasons"] == ["retiredInterval"]


def test_rewithdrawn_interval_replaces_its_own_record(bundle, tmp_path, monkeypatch):
    _, _, inv, _ = bundle
    url = urls_for(inv)[0]
    key = key_of(url)
    source = Source(monkeypatch, inv)
    previous = tmp_path / "run0"
    run(bundle, previous)
    for index, (validity, reported) in enumerate([(-1, 5), (1, 6), (-1, 7)]):
        source.rows[url] = [reading(url, validity=validity, reported=reported)]
        output = tmp_path / f"run{index + 1}"
        run(bundle, output, previous=previous, now=at(index + 1))
        previous = output
    _, channels, rows, _ = load(bundle, previous)
    assert key not in rows
    assert [w["reportedAt"] for w in channels[key]["withdrawals"]] == [
        "2026-11-01T17:00:00+01:00"
    ]
    assert channels[key]["retiredBefore"] == {}


@pytest.mark.parametrize(
    "corruption",
    ["tooMany", "belowFloor", "latestBelowFloor", "foreignFloor", "list", "naive"],
)
def test_retired_floor_and_window_are_strictly_validated(
    bundle, tmp_path, monkeypatch, corruption
):
    _, cat, inv, digest = bundle
    urls = urls_for(inv)
    url = urls[0]
    source = Source(monkeypatch, inv)
    first = tmp_path / "first"
    run(bundle, first)
    source.rows[url] = [reading(url, validity=-1, reported=5), reading(url, 11)]
    second = tmp_path / "second"
    run(bundle, second, previous=first, now=LATER)
    state, body, _ = resilience.read_run(second, cat, inv, digest)
    row = state["channels"][0]
    tombstone = row["withdrawals"][0]
    resilience.validate_state(state, cat, inv, digest, body)
    if corruption == "tooMany":
        row["withdrawals"] = [
            {
                **tombstone,
                "observedFrom": f"2026-10-{day:02d}T00:00:00+01:00",
                "observedTo": f"2026-10-{day:02d}T01:00:00+01:00",
            }
            for day in range(1, resilience.MAX_WITHDRAWALS + 2)
        ]
    elif corruption == "belowFloor":
        row["retiredBefore"] = {url: tombstone["observedTo"]}
    elif corruption == "latestBelowFloor":
        row["retiredBefore"] = {url: row["latest"]["observedTo"]}
        row["withdrawals"] = []
    elif corruption == "foreignFloor":
        row["retiredBefore"] = {urls[1]: "2026-10-01T00:00:00+01:00"}
    elif corruption == "naive":
        row["retiredBefore"] = {url: "2026-10-01T00:00:00"}
    else:
        row["retiredBefore"] = []
    with pytest.raises((ValueError, TypeError)):
        resilience.validate_state(state, cat, inv, digest, body)


def test_worst_case_withdrawal_evidence_fits_the_state_byte_limit():
    cat, inv, manifest_digest = registry.load_registry(
        Path("data/eea/registries/v1/manifest.json")
    )
    snapshot = Path("src/data/observations.json").read_bytes()
    state = resilience.initial_state(cat, inv, manifest_digest, snapshot, NOW)
    approved = catalog_tools.validate_channel_inventory(inv, cat)
    for row in state["channels"]:
        streams = approved[row["stationId"], row["pollutant"]]
        url = max(streams, key=lambda s: len(s["sourceUrl"]))["sourceUrl"]
        row["hasObserved"] = True
        row["retiredBefore"] = {
            s["sourceUrl"]: "2020-01-01T01:00:00+01:00" for s in streams
        }
        row["withdrawals"] = [
            {
                "sourceUrl": url,
                "observedFrom": f"2020-02-{day:02d}T00:00:00+01:00",
                "observedTo": f"2020-02-{day:02d}T01:00:00+01:00",
                "reportedAt": f"2020-02-{day:02d}T02:00:00+01:00",
                "confirmedAt": NOW,
                "etag": '"0x8DF4796893AAA50-long-enough-etag-value"',
                "payloadSha256": "c" * 64,
            }
            for day in range(1, resilience.MAX_WITHDRAWALS + 1)
        ]
    resilience.validate_state(state, cat, inv, manifest_digest, snapshot)
    size = len(builder.encode(state))
    assert size < catalog_tools.MAX_DOCUMENT_BYTES * 3 // 4


def test_new_lineage_can_bootstrap_from_a_controlled_snapshot(
    bundle, tmp_path, monkeypatch
):
    urls = urls_for(bundle[2])
    source = Source(monkeypatch, bundle[2])
    first = tmp_path / "first"
    run(bundle, first)
    second = tmp_path / "second"
    run(bundle, second, previous=first, now=LATER)
    seed = second / "observations.json"
    assert json.loads(seed.read_bytes())["publication"]["generation"] == 2
    source.errors = {url: TimeoutError("synthetic") for url in urls[:7]}
    blocked = tmp_path / "blocked"
    report = run(bundle, blocked, seed=seed, now=LAST)
    state, channels, _, body = load(bundle, blocked)
    assert report["decision"] == "blocked"
    assert state["generation"] == 1 and state["previousRunSha256"] is None
    assert body == seed.read_bytes()
    assert channels[key_of(urls[0])]["latest"]["confirmedAt"] is None
    assert channels[key_of(urls[7])]["latest"]["confirmedAt"] == LAST
    source.errors.clear()
    resumed = tmp_path / "resumed"
    report = run(bundle, resumed, previous=blocked, now=at(3))
    state, _, rows, body = load(bundle, resumed)
    assert report["decision"] == "normal"
    assert len(rows) == 12
    assert json.loads(body)["publication"]["generation"] == 2


def test_payload_download_keeps_bounded_retry_and_returns_etag(monkeypatch):
    url = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0008R_00001_100.parquet"
    attempts = []

    def read(request, limit):
        attempts.append((request.full_url, limit))
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 503, "x", {}, None)
        return b"payload", '"etag"'

    monkeypatch.setattr(importer, "read_http_response", read)
    monkeypatch.setattr(importer.time, "sleep", lambda seconds: None)
    assert importer.fetch_parquet_payload(url) == (b"payload", '"etag"')
    assert attempts == [(url, importer.MAX_PARQUET_BYTES)] * 2
    with pytest.raises(ValueError):
        importer.fetch_parquet_payload(url.replace("/RO/", "/XX/"))
