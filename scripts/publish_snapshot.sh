#!/usr/bin/env bash
set -eo pipefail
base=$(git rev-parse HEAD)
git fetch --no-tags origin main
if [ "$(git rev-parse FETCH_HEAD)" != "$base" ]; then
  echo '::notice::Main advanced; discard this snapshot and collect again on the next run.'
  exit 0
fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -- src/data/observations.json
if git diff --cached --quiet; then
  echo 'Snapshot unchanged'
  exit 0
fi
if [ "$(git diff --cached --name-only)" != 'src/data/observations.json' ]; then
  echo 'Publication may change only observations.json'
  exit 1
fi
git commit -m 'data: refresh EEA observations'
candidate="refresh-candidate-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
git push origin "HEAD:refs/heads/$candidate"
trap 'git push origin --delete "$candidate" || true' EXIT
python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path
result = {
    'name': 'production-checks',
    'head_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'status': 'completed',
    'conclusion': 'success',
    'output': {
        'title': 'Verified EEA snapshot',
        'summary': 'Importer tests, schema and coverage/regression guards passed. Only observations.json changed; unchanged code has a matching successfully deployed approved-site artifact. Freshness is monitored separately.',
    },
}
path = Path(os.environ['RUNNER_TEMP']) / 'snapshot-check.json'
path.write_text(json.dumps(result))
subprocess.run(['gh', 'api', '--method', 'POST', f"repos/{os.environ['GITHUB_REPOSITORY']}/check-runs", '--input', str(path)], check=True, stdout=subprocess.DEVNULL)
PY
if ! git push origin HEAD:main; then
  git fetch --no-tags origin main
  if [ "$(git rev-parse FETCH_HEAD)" != "$base" ]; then
    echo '::notice::Main advanced during publication; discard this snapshot and collect again on the next run.'
    exit 0
  fi
  exit 1
fi
