import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


def publication_step():
    workflow = Path(".github/workflows/refresh-eea.yml").read_text()
    assert workflow.count("bash scripts/publish_snapshot.sh\n") == 2
    return Path("scripts/publish_snapshot.sh").read_text()


@pytest.mark.parametrize(
    "scenario", ["publish", "stale", "race", "push_failure", "fetch_failure"]
)
def test_publication_preserves_main_and_distinguishes_obsolete_candidates(
    tmp_path, scenario
):
    real_git = shutil.which("git")

    def git(*args, cwd=tmp_path):
        return subprocess.check_output([real_git, *args], cwd=cwd, text=True).strip()

    remote = tmp_path / "remote.git"
    git("init", "--bare", "--initial-branch=main", str(remote))
    work = tmp_path / "work"
    git("clone", str(remote), str(work))
    git("config", "user.name", "Test", cwd=work)
    git("config", "user.email", "test@example.com", cwd=work)
    (work / "src/data").mkdir(parents=True)
    snapshot = work / "src/data/observations.json"
    snapshot.write_text("baseline")
    git("add", ".", cwd=work)
    git("commit", "-qm", "baseline", cwd=work)
    git("push", "origin", "main", cwd=work)
    base = git("rev-parse", "HEAD", cwd=work)
    rival = tmp_path / "rival"
    git("clone", str(remote), str(rival))
    git("config", "user.name", "Test", cwd=rival)
    git("config", "user.email", "test@example.com", cwd=rival)
    (rival / "src/data/observations.json").write_text("newer published snapshot")
    git("add", ".", cwd=rival)
    git("commit", "-qm", "competing update", cwd=rival)
    competing = git("rev-parse", "HEAD", cwd=rival)
    if scenario == "stale":
        git("push", "origin", "main", cwd=rival)
    snapshot.write_text("candidate snapshot")
    launcher = tmp_path / "bin"
    launcher.mkdir()
    wrapper = launcher / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2 $3" = "push origin HEAD:main" ]; then\n'
        '  if [ "$SCENARIO" = race ]; then\n'
        f"    {shlex.quote(real_git)} -C {shlex.quote(str(rival))} push origin main || exit 1\n"
        '  elif [ "$SCENARIO" = push_failure ]; then exit 1; fi\n'
        "fi\n"
        'if [ "$1" = fetch ] && [ "$SCENARIO" = fetch_failure ]; then exit 1; fi\n'
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    wrapper.chmod(0o700)
    calls = tmp_path / "check-calls"
    gh = launcher / "gh"
    gh.write_text(f'#!/bin/sh\nprintf "check\\n" >> {shlex.quote(str(calls))}\n')
    gh.chmod(0o700)
    result = subprocess.run(
        ["bash", "-e", "-c", publication_step()],
        cwd=work,
        env=os.environ
        | {
            "PATH": f"{launcher}{os.pathsep}{os.environ['PATH']}",
            "SCENARIO": scenario,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REPOSITORY": "owner/repo",
            "RUNNER_TEMP": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    head = git("--git-dir", str(remote), "rev-parse", "main")
    assert "refresh-candidate-" not in git(
        "--git-dir", str(remote), "for-each-ref", "refs/heads"
    )
    if scenario == "publish":
        assert result.returncode == 0, result.stderr
        assert (
            git("--git-dir", str(remote), "show", "main:src/data/observations.json")
            == "candidate snapshot"
        )
        assert git("--git-dir", str(remote), "rev-parse", "main^") == base
        assert calls.read_text() == "check\n"
    elif scenario in ("stale", "race"):
        assert result.returncode == 0, result.stderr
        assert "::notice::Main advanced" in result.stdout
        assert head == competing
        assert (
            git("--git-dir", str(remote), "show", "main:src/data/observations.json")
            == "newer published snapshot"
        )
        assert calls.exists() == (scenario == "race")
    else:
        assert result.returncode != 0
        assert head == base
        assert calls.exists() == (scenario == "push_failure")


@pytest.mark.parametrize(
    "scenario",
    [
        "initialize",
        "continue",
        "missing",
        "rejected",
        "race",
        "reinitialize",
        "unreadable",
        "unreadable-initialize",
    ],
)
def test_controlled_state_is_persisted_only_after_lineage_verification(
    tmp_path, scenario
):
    real_git = shutil.which("git")

    def git(*args, cwd=tmp_path):
        return subprocess.check_output([real_git, *args], cwd=cwd, text=True).strip()

    remote = tmp_path / "remote.git"
    git("init", "--bare", "--initial-branch=main", str(remote))
    work = tmp_path / "work"
    git("clone", str(remote), str(work))
    git("config", "user.name", "Test", cwd=work)
    git("config", "user.email", "test@example.com", cwd=work)
    (work / "README").write_text("code")
    git("add", ".", cwd=work)
    git("commit", "-qm", "baseline", cwd=work)
    git("push", "origin", "main", cwd=work)
    main = git("rev-parse", "HEAD", cwd=work)
    existing = scenario not in ("initialize", "missing")
    if existing:
        (work / "run").mkdir()
        (work / "run/state.json").write_text("previous state")
        (work / "run/collection-report.json").write_text("previous report")
        git("checkout", "-q", "--orphan", "seed", cwd=work)
        git("rm", "-q", "--cached", "README", cwd=work)
        git("add", "run", cwd=work)
        git("commit", "-qm", "state", cwd=work)
        git("push", "origin", "seed:refresh-state", cwd=work)
        git("checkout", "-qf", "main", cwd=work)
    before = (
        git("--git-dir", str(remote), "rev-parse", "refresh-state")
        if existing
        else None
    )
    rival = tmp_path / "rival"
    if scenario == "race":
        git("clone", "-q", "--branch", "refresh-state", str(remote), str(rival))
        git("config", "user.name", "Test", cwd=rival)
        git("config", "user.email", "test@example.com", cwd=rival)
        (rival / "run/state.json").write_text("rival state")
        git("commit", "-qam", "rival", cwd=rival)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for name in ("state.json", "collection-report.json", "observations.json"):
        (candidate / name).write_text(f"new {name}")
    launcher = tmp_path / "bin"
    launcher.mkdir()
    calls = tmp_path / "uv-calls"
    uv = launcher / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
        'previous=""\n'
        'while [ "$#" -gt 0 ]; do [ "$1" = --previous-run ] && previous="$2"; shift; done\n'
        f'[ -z "$previous" ] || cat "$previous/state.json" >> {shlex.quote(str(calls))}\n'
        '[ "$SCENARIO" = rejected ] && exit 1\n'
        'if [ "$SCENARIO" = race ]; then\n'
        f"  {shlex.quote(real_git)} -C {shlex.quote(str(rival))} push -q origin refresh-state || exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    uv.chmod(0o700)
    wrapper = launcher / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = ls-remote ] && [ "${SCENARIO#unreadable}" != "$SCENARIO" ]; then exit 128; fi\n'
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    wrapper.chmod(0o700)
    environment = os.environ | {
        "PATH": f"{launcher}{os.pathsep}{os.environ['PATH']}",
        "SCENARIO": scenario,
        "GITHUB_RUN_ID": "123",
    }
    initialize = scenario in ("initialize", "reinitialize", "unreadable-initialize")
    result = subprocess.run(
        [
            "bash",
            str(Path("scripts/persist_refresh_state.sh").resolve()),
            str(candidate),
            "true" if initialize else "false",
        ],
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert git("--git-dir", str(remote), "rev-parse", "main") == main
    assert git("status", "--porcelain", cwd=work) == ""
    refs = git("--git-dir", str(remote), "for-each-ref", "--format=%(refname)")
    if scenario in ("missing", "rejected", "race") or "unreadable" in scenario:
        assert result.returncode != 0
        if scenario == "missing":
            assert "refresh-state" not in refs
            assert not calls.exists()
        elif scenario == "race":
            head = git("--git-dir", str(remote), "rev-parse", "refresh-state")
            assert head == git("rev-parse", "HEAD", cwd=rival)
        else:
            head = git("--git-dir", str(remote), "rev-parse", "refresh-state")
            assert head == before
            assert calls.exists() == (scenario == "rejected")
        return
    assert result.returncode == 0, result.stderr
    head = git("--git-dir", str(remote), "rev-parse", "refresh-state")
    listed = git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", head)
    assert listed.splitlines() == [
        "run/collection-report.json",
        "run/observations.json",
        "run/state.json",
    ]
    assert (
        git("--git-dir", str(remote), "show", f"{head}:run/state.json")
        == "new state.json"
    )
    parents = git("--git-dir", str(remote), "rev-list", "--parents", "-1", head)
    assert parents.split()[1:] == ([before] if existing else [])
    recorded = calls.read_text()
    assert f"--run {candidate}" in recorded
    assert ("--previous-run" in recorded) == (not initialize)
    assert ("previous state" in recorded) == (not initialize)
    assert "--export" not in recorded
