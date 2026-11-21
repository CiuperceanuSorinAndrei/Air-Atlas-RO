# ruff: noqa: F811
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_eea_candidates as builder
from scripts import publish_controlled_run as publisher
from tests.test_eea_resilience import (  # noqa: F401
    LAST,
    LATER,
    NOW,
    Source,
    accepted_bytes,
    at,
    bundle,
    discovery_body,
    no_network,
    reading,
    run,
    seeded,
    urls_for,
)


def approval(bundle, tmp_path, **changes):
    document = {
        "schemaVersion": 1,
        "registryManifestSha256": bundle[3],
        "approvedAt": "2026-11-20T00:00:00+00:00",
        "basis": "Owner-approved activation review.",
    }
    document.update(changes)
    path = tmp_path / "production-approval.json"
    path.write_bytes(builder.encode(document))
    return path


def lineage(bundle, tmp_path, monkeypatch):
    source = Source(monkeypatch, bundle[2])
    seed = seeded(bundle, tmp_path)
    first = tmp_path / "first"
    run(bundle, first, seed=seed)
    second = tmp_path / "second"
    run(bundle, second, previous=first, now=LATER)
    return source, seed, first, second


def test_run_must_continue_the_persisted_lineage(bundle, tmp_path, monkeypatch):
    manifest = bundle[0] / "manifest.json"
    _, _, first, second = lineage(bundle, tmp_path, monkeypatch)
    fork = tmp_path / "fork"
    run(bundle, fork, previous=first, now=LAST)
    third = tmp_path / "third"
    run(bundle, third, previous=second, now=LAST)
    assert (
        publisher.verify_run(manifest, first)
        == (first / "observations.json").read_bytes()
    )
    assert publisher.verify_run(manifest, second, first) is not None
    for run_path, previous in (
        (second, None),
        (fork, second),
        (third, first),
        (first, second),
        (second, second),
    ):
        with pytest.raises(ValueError, match="lineage"):
            publisher.verify_run(manifest, run_path, previous)
    state = json.loads((second / "state.json").read_bytes())
    state["generation"] = 3
    (second / "state.json").write_bytes(builder.encode(state))
    with pytest.raises(ValueError):
        publisher.verify_run(manifest, second, first)


def test_export_replaces_only_an_older_public_snapshot(bundle, tmp_path, monkeypatch):
    manifest = bundle[0] / "manifest.json"
    _, seed, first, second = lineage(bundle, tmp_path, monkeypatch)
    approved = approval(bundle, tmp_path)
    target = tmp_path / "public.json"
    target.write_bytes(seed.read_bytes())
    snapshot = (second / "observations.json").read_bytes()
    assert publisher.export_snapshot(manifest, approved, second, target) == "exported"
    assert target.read_bytes() == snapshot
    assert publisher.export_snapshot(manifest, approved, second, target) == "unchanged"
    assert publisher.export_snapshot(manifest, approved, first, target) == "publicNewer"
    assert target.read_bytes() == snapshot
    newer_legacy = json.loads(seed.read_bytes())
    for row in newer_legacy["observations"]:
        row["ingestedAt"] = LAST
    target.write_bytes(json.dumps(newer_legacy).encode())
    kept = target.read_bytes()
    assert (
        publisher.export_snapshot(manifest, approved, second, target) == "publicNewer"
    )
    assert target.read_bytes() == kept
    assert sorted(path.name for path in tmp_path.glob(".observations-*")) == []
    state = json.loads((second / "state.json").read_bytes())
    state["generation"] = 3
    (second / "state.json").write_bytes(builder.encode(state))
    target.write_bytes(seed.read_bytes())
    with pytest.raises(ValueError):
        publisher.export_snapshot(manifest, approved, second, target)
    assert target.read_bytes() == seed.read_bytes()


def test_blocked_run_exports_nothing_and_pending_snapshot_is_mirrored_later(
    bundle, tmp_path, monkeypatch
):
    manifest = bundle[0] / "manifest.json"
    source, seed, first, second = lineage(bundle, tmp_path, monkeypatch)
    approved = approval(bundle, tmp_path)
    target = tmp_path / "public.json"
    target.write_bytes(seed.read_bytes())
    source.errors = {url: TimeoutError("synthetic") for url in urls_for(bundle[2])[:7]}
    blocked = tmp_path / "blocked"
    report = run(bundle, blocked, previous=second, now=LAST)
    assert report["decision"] == "blocked"
    pending = (second / "observations.json").read_bytes()
    assert publisher.export_snapshot(manifest, approved, blocked, target) == "exported"
    assert target.read_bytes() == pending
    assert publisher.export_snapshot(manifest, approved, blocked, target) == "unchanged"


def test_empty_corrective_snapshot_is_exported(bundle, tmp_path, monkeypatch):
    manifest = bundle[0] / "manifest.json"
    urls = urls_for(bundle[2])
    source = Source(monkeypatch, bundle[2])
    first = tmp_path / "first"
    run(bundle, first)
    target = tmp_path / "public.json"
    target.write_bytes((first / "observations.json").read_bytes())
    for url in urls:
        source.rows[url] = [reading(url, validity=-1, reported=5)]
    second = tmp_path / "second"
    report = run(bundle, second, previous=first, now=LATER)
    assert report["decision"] == "corrective"
    assert (
        publisher.export_snapshot(manifest, approval(bundle, tmp_path), second, target)
        == "exported"
    )
    assert json.loads(target.read_bytes())["observations"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"registryManifestSha256": "0" * 64},
        {"schemaVersion": 2},
        {"approvedAt": "2026-11-20T00:00:00"},
        {"basis": ""},
        {"extra": True},
    ],
)
def test_export_requires_approval_of_the_exact_registry(
    bundle, tmp_path, monkeypatch, changes
):
    manifest = bundle[0] / "manifest.json"
    _, seed, first, second = lineage(bundle, tmp_path, monkeypatch)
    target = tmp_path / "public.json"
    target.write_bytes(seed.read_bytes())
    with pytest.raises((ValueError, TypeError)):
        publisher.export_snapshot(
            manifest, approval(bundle, tmp_path, **changes), second, target
        )
    with pytest.raises(OSError):
        publisher.export_snapshot(manifest, tmp_path / "missing.json", second, target)
    assert target.read_bytes() == seed.read_bytes()


def test_cli_reports_decision_and_fails_closed(bundle, tmp_path, monkeypatch):
    manifest = bundle[0] / "manifest.json"
    _, seed, first, second = lineage(bundle, tmp_path, monkeypatch)
    target = tmp_path / "public.json"
    target.write_bytes(seed.read_bytes())
    base = [sys.executable, "-m", "scripts.publish_controlled_run"]
    common = ["--registry-manifest", str(manifest), "--run", str(second)]
    export = ["--production-approval", str(approval(bundle, tmp_path))]
    export += ["--export", str(target)]

    def call(*arguments):
        return subprocess.run(
            [*base, *common, *arguments], capture_output=True, text=True, timeout=60
        )

    verified = call("--previous-run", str(first))
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == {
        "blockedBy": None,
        "budget": {"degradedChannels": 0, "degradedStations": 0, "withinBudget": True},
        "decision": "normal",
        "result": "verified",
    }
    assert target.read_bytes() == seed.read_bytes()
    exported = call(*export)
    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["result"] == "exported"
    assert target.read_bytes() == (second / "observations.json").read_bytes()
    target.write_bytes(seed.read_bytes())
    for arguments in (
        (),
        ("--export", str(target)),
        ("--previous-run", str(first), *export),
        ("--previous-run", str(tmp_path / "absent")),
        ("--production-approval", str(tmp_path / "absent"), "--export", str(target)),
    ):
        failed = call(*arguments)
        assert failed.returncode != 0
        assert "Traceback" not in failed.stderr
    assert target.read_bytes() == seed.read_bytes()


def test_persist_script_verifies_real_bundles_before_pushing(
    bundle, tmp_path, monkeypatch
):
    _, _, first, second = lineage(bundle, tmp_path, monkeypatch)
    fork = tmp_path / "fork"
    run(bundle, fork, previous=first, now=LAST)
    third = tmp_path / "third"
    run(bundle, third, previous=second, now=LAST)

    def git(*args, cwd=tmp_path):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", "--initial-branch=main", str(remote))
    work = tmp_path / "work"
    git("clone", "-q", str(remote), str(work))
    launcher = tmp_path / "bin"
    launcher.mkdir()
    uv = launcher / "uv"
    uv.write_text(
        f'#!/bin/sh\n[ "$1 $2 $3" = "run --locked python" ] || exit 2\nshift 3\nexec {shlex.quote(sys.executable)} "$@"\n'
    )
    uv.chmod(0o700)
    script = Path("scripts/persist_refresh_state.sh").resolve()
    environment = os.environ | {
        "PATH": f"{launcher}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(Path.cwd()),
        "ATLAS_REGISTRY_MANIFEST": str(bundle[0] / "manifest.json"),
    }

    def persist(run_path, initialize):
        return subprocess.run(
            ["bash", str(script), str(run_path), initialize],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def head():
        return git("--git-dir", str(remote), "rev-parse", "refresh-state")

    assert persist(second, "true").returncode != 0
    assert persist(first, "true").returncode == 0
    initial = head()
    for rejected in (third, first):
        assert persist(rejected, "false").returncode != 0
        assert head() == initial
    assert persist(second, "false").returncode == 0
    assert head() != initial
    continued = head()
    assert persist(fork, "false").returncode != 0
    assert head() == continued
    result = persist(third, "false")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[0])["result"] == "verified"
    stored = git("--git-dir", str(remote), "show", f"{head()}:run/state.json")
    assert stored.encode() + b"\n" == (third / "state.json").read_bytes()
    assert git("--git-dir", str(remote), "rev-list", "--count", head()) == "3"
