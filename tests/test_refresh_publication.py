import os
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def publication_step():
    workflow = Path(".github/workflows/refresh-eea.yml").read_text().splitlines()
    start = workflow.index("      - name: Publish verified snapshot")
    start = workflow.index("        run: |", start)
    lines = []
    for line in workflow[start + 1 :]:
        if line and not line.startswith("          "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines))


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
