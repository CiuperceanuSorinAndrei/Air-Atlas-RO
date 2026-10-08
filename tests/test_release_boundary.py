import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import approved_site
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
