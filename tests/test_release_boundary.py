import json
import os
import shlex
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError

import pytest

from scripts import approved_site, check_served_data
from scripts.check_served_data import assess_snapshot


def test_code_fingerprint_ignores_only_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    (tmp_path / "src/data").mkdir(parents=True)
    snapshot = tmp_path / "src/data/observations.json"
    code = tmp_path / "app.js"
    snapshot.write_text("old")
    code.write_text("accepted")

    def commit():
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )

    commit()
    accepted = approved_site.code_fingerprint()
    snapshot.write_text("new")
    commit()
    assert approved_site.code_fingerprint() == accepted
    code.write_text("unapproved")
    commit()
    assert approved_site.code_fingerprint() != accepted


@pytest.mark.parametrize(
    "change",
    [
        {"path": ".github/workflows/checks.yml"},
        {"head_branch": "feature"},
        {"event": "workflow_run"},
        {"conclusion": "failure"},
        {"status": "in_progress"},
    ],
)
def test_rejects_unapproved_artifact_provenance(monkeypatch, change):
    run = {
        "path": ".github/workflows/deploy-pages.yml",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    } | change
    artifact = {
        "name": "approved-site-test",
        "expired": False,
        "workflow_run": {"id": 12},
    }
    monkeypatch.setattr(
        approved_site,
        "github_json",
        lambda path: {"artifacts": [artifact]} if "artifacts?" in path else run,
    )
    with pytest.raises(ValueError, match="release code first"):
        approved_site.find_approved_site("owner/repo", "test")


def test_approved_artifact_and_expiration(monkeypatch):
    artifact = {
        "name": "approved-site-test",
        "expired": False,
        "workflow_run": {"id": 12},
    }
    run = {
        "path": ".github/workflows/deploy-pages.yml",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    monkeypatch.setattr(
        approved_site,
        "github_json",
        lambda path: {"artifacts": [artifact]} if "artifacts?" in path else run,
    )
    assert approved_site.find_approved_site("owner/repo", "test") == {
        "artifact-name": "approved-site-test",
        "run-id": "12",
    }
    artifact["expired"] = True
    with pytest.raises(ValueError):
        approved_site.find_approved_site("owner/repo", "test")


def snapshot_at(now):
    row = json.loads(Path("src/data/observations.json").read_text())["observations"][0]
    row |= {
        "observedFrom": (now - timedelta(hours=1)).isoformat(),
        "observedTo": now.isoformat(),
        "reportedAt": now.isoformat(),
        "ingestedAt": now.isoformat(),
        "aggregationType": "hour",
    }
    return {
        "observations": [row],
        "importSummary": {"attempted": 1, "imported": 1, "skipped": 0, "failed": 0},
    }


def test_monitor_distinguishes_measurement_age_and_import_age():
    now = datetime(2026, 10, 8, 10, tzinfo=UTC)
    snapshot = snapshot_at(now)
    report, degraded = assess_snapshot(snapshot, now + timedelta(minutes=30))
    assert not degraded and report["recent_readings"] == 1
    report, degraded = assess_snapshot(snapshot, now + timedelta(hours=2))
    assert degraded and report["recent_readings"] == 1
    snapshot["observations"][0]["ingestedAt"] = (now + timedelta(hours=7)).isoformat()
    report, degraded = assess_snapshot(snapshot, now + timedelta(hours=7))
    assert degraded and report["recent_readings"] == 0


def test_monitor_rejects_invalid_served_data():
    now = datetime(2026, 10, 8, 10, tzinfo=UTC)
    snapshot = snapshot_at(now)
    snapshot["observations"][0]["unit"] = "unsupported"
    with pytest.raises(ValueError):
        assess_snapshot(snapshot, now)


@pytest.mark.parametrize(
    "age, expected_recent",
    [
        (timedelta(0), True),
        (timedelta(hours=1), True),
        (timedelta(hours=2, microseconds=-1), True),
        (timedelta(hours=2), False),
        (timedelta(hours=5, minutes=22), False),
        (timedelta(hours=-1), False),
    ],
)
def test_monitor_reports_ingestion_age_and_exact_boundaries(age, expected_recent):
    now = datetime(2026, 10, 10, 6, 25, tzinfo=UTC)
    snapshot = snapshot_at(now - timedelta(hours=5, minutes=30))
    snapshot["observations"][0]["ingestedAt"] = (now - age).isoformat()
    report, degraded = assess_snapshot(snapshot, now)
    assert report["recent_readings"] == 1
    assert report["ingestion_age_hours"] == pytest.approx(age.total_seconds() / 3600)
    assert report["ingestion_is_recent"] is expected_recent
    assert degraded is (not expected_recent)


def test_monitor_accepts_equivalent_ingestion_timezones():
    now = datetime(2026, 10, 10, 6, 25, tzinfo=UTC)
    snapshot = snapshot_at(now - timedelta(hours=1))
    original = assess_snapshot(snapshot, now)
    snapshot["observations"][0]["ingestedAt"] = "2026-10-10T08:25:00+03:00"
    report, degraded = assess_snapshot(snapshot, now)
    assert report["ingestion_age_hours"] == original[0]["ingestion_age_hours"] == 1
    assert report["ingestion_is_recent"] is True
    assert degraded is original[1] is False


def test_monitor_reports_recent_ingestion_with_old_measurements():
    now = datetime(2026, 10, 10, 6, 25, tzinfo=UTC)
    snapshot = snapshot_at(now - timedelta(hours=7))
    snapshot["observations"][0]["ingestedAt"] = now.isoformat()
    report, degraded = assess_snapshot(snapshot, now)
    assert report["recent_readings"] == 0
    assert report["ingestion_age_hours"] == 0
    assert report["ingestion_is_recent"] is True
    assert degraded


def test_monitor_rejects_failed_imports_in_otherwise_valid_snapshot():
    now = datetime(2026, 10, 10, 6, 25, tzinfo=UTC)
    snapshot = snapshot_at(now)
    snapshot["importSummary"].update(attempted=2, failed=1)
    with pytest.raises(ValueError, match="failed imports"):
        assess_snapshot(snapshot, now)


class ServedResponse:
    def __init__(self, payload, url, status=200):
        self.payload = payload
        self.url = url
        self.status = status
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        self.read_limits.append(limit)
        return self.payload[:limit]


@pytest.fixture
def served_request(monkeypatch):
    url = "https://ciuperceanusorinandrei.github.io/Air-Atlas-RO/observations.json?monitor=test"
    monkeypatch.setattr(sys, "argv", ["check_served_data", url])
    now = datetime(2026, 10, 10, 6, 25, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return now

    monkeypatch.setattr(check_served_data, "datetime", FixedDatetime)
    response = ServedResponse(json.dumps(snapshot_at(now)).encode(), url)

    def fetch(request, timeout):
        assert request.full_url == url
        assert request.get_header("Cache-control") == "no-cache"
        assert timeout == 30
        return response

    monkeypatch.setattr(check_served_data, "urlopen", fetch)
    return now, response


@pytest.mark.parametrize("age", [0, 3])
def test_monitor_cli_prints_report_and_exits_only_when_degraded(
    served_request, capsys, age
):
    now, response = served_request
    snapshot = snapshot_at(now - timedelta(hours=4))
    snapshot["observations"][0]["ingestedAt"] = (now - timedelta(hours=age)).isoformat()
    response.payload = json.dumps(snapshot).encode()
    if age:
        with pytest.raises(SystemExit) as error:
            check_served_data.main()
        assert str(error.value) == (
            "Degraded: recent_readings=1; ingestion_age_hours=3.00 ore; "
            "ingestion_is_recent=False."
        )
    else:
        check_served_data.main()
    report = json.loads(capsys.readouterr().out)
    assert report["recent_readings"] == 1
    assert report["ingestion_age_hours"] == pytest.approx(age, abs=0.01)
    assert report["ingestion_is_recent"] is (age == 0)
    assert response.read_limits == [8 * 1024 * 1024 + 1]


@pytest.mark.parametrize("status", [429, 500])
def test_monitor_cli_rejects_unsuccessful_http_response(served_request, status):
    _, response = served_request
    response.status = status
    with pytest.raises(ValueError, match="Unexpected public snapshot response"):
        check_served_data.main()
    assert not response.read_limits


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/observations.json",
        "https://ciuperceanusorinandrei.github.io/Air-Atlas-RO/other.json",
    ],
)
def test_monitor_cli_rejects_redirected_response(served_request, url):
    _, response = served_request
    response.url = url
    with pytest.raises(ValueError, match="Unexpected public snapshot response"):
        check_served_data.main()
    assert not response.read_limits


@pytest.mark.parametrize(
    "payload, error, message",
    [
        (b"x" * (8 * 1024 * 1024 + 1), ValueError, "size limit"),
        (b"not JSON", json.JSONDecodeError, None),
    ],
)
def test_monitor_cli_rejects_oversized_or_malformed_body(
    served_request, capsys, payload, error, message
):
    _, response = served_request
    response.payload = payload
    with pytest.raises(error, match=message):
        check_served_data.main()
    assert not capsys.readouterr().out


def test_monitor_cli_propagates_network_failure(served_request, monkeypatch):
    def fail(request, timeout):
        raise URLError("connection unavailable")

    monkeypatch.setattr(check_served_data, "urlopen", fail)
    with pytest.raises(URLError, match="connection unavailable"):
        check_served_data.main()


def test_monitor_cli_rejects_foreign_origin_before_network(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["check_served_data", "https://example.com/observations.json"]
    )
    monkeypatch.setattr(
        check_served_data,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("network called"),
    )
    with pytest.raises(ValueError, match="public Atlas Pages origin/path"):
        check_served_data.main()


@pytest.mark.parametrize("valid", [True, False])
def test_data_deployment_step_preserves_approved_assets(tmp_path, valid):
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github/workflows/deploy-pages.yml").read_text().splitlines()
    start = workflow.index("      - name: Validate and replace only the snapshot")
    assert workflow[start + 1] == "        run: |"
    lines = []
    for line in workflow[start + 2 :]:
        if line and not line.startswith("          "):
            break
        lines.append(line)
    step = textwrap.dedent("\n".join(lines))
    assert step.strip()
    (tmp_path / "scripts").symlink_to(root / "scripts", target_is_directory=True)
    (tmp_path / "src/data").mkdir(parents=True)
    snapshot = snapshot_at(datetime(2026, 10, 10, 6, tzinfo=UTC))
    if not valid:
        snapshot["observations"][0]["unit"] = "unsupported"
    candidate = json.dumps(snapshot).encode()
    (tmp_path / "src/data/observations.json").write_bytes(candidate)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    for name, content in {
        "index.html": b"accepted HTML",
        "assets/app.js": b"accepted JS",
        "assets/app.css": b"accepted CSS",
        "observations.json": b"previous snapshot",
    }.items():
        (dist / name).write_bytes(content)
    before = {
        p.relative_to(dist): p.read_bytes() for p in dist.rglob("*") if p.is_file()
    }
    launcher = tmp_path / "bin"
    launcher.mkdir()
    uv = launcher / "uv"
    uv.write_text(
        f'#!/bin/sh\n[ "$1 $2 $3" = "run --locked python" ] || exit 2\n'
        f'shift 3\nexec {shlex.quote(sys.executable)} -B "$@"\n'
    )
    uv.chmod(0o700)
    result = subprocess.run(
        ["bash", "-e", "-c", step],
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{launcher}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    after = {
        p.relative_to(dist): p.read_bytes() for p in dist.rglob("*") if p.is_file()
    }
    if valid:
        assert result.returncode == 0, result.stderr
        assert after == before | {Path("observations.json"): candidate}
    else:
        assert result.returncode != 0
        assert "Unsupported EEA concentration unit" in result.stderr
        assert after == before
