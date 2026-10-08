import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def code_fingerprint(ref="HEAD"):
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", ref])
    entries = [
        entry
        for entry in tree.split(b"\0")
        if entry and entry.split(b"\t", 1)[1] != b"src/data/observations.json"
    ]
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def github_json(path):
    return json.loads(subprocess.check_output(["gh", "api", path], timeout=30))


def find_approved_site(repository, fingerprint):
    name = f"approved-site-{fingerprint}"
    for page in range(1, 6):
        artifacts = github_json(
            f"repos/{repository}/actions/artifacts?name={name}&per_page=100&page={page}"
        )["artifacts"]
        for artifact in artifacts:
            if artifact["name"] != name or artifact["expired"]:
                continue
            run_id = artifact["workflow_run"]["id"]
            run = github_json(f"repos/{repository}/actions/runs/{run_id}")
            if (
                run["path"] == ".github/workflows/deploy-pages.yml"
                and run["head_branch"] == "main"
                and run["event"] in ("push", "workflow_dispatch")
                and run["status"] == "completed"
                and run["conclusion"] == "success"
            ):
                return {"artifact-name": name, "run-id": str(run_id)}
        if len(artifacts) < 100:
            break
    raise ValueError(
        "No deployed, approved site for this code tree; release code first."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fingerprint", action="store_true")
    args = parser.parse_args()
    fingerprint = code_fingerprint()
    output = {"fingerprint": fingerprint}
    if not args.fingerprint:
        output.update(find_approved_site(os.environ["GITHUB_REPOSITORY"], fingerprint))
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            for key, value in output.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(output))


if __name__ == "__main__":
    main()
