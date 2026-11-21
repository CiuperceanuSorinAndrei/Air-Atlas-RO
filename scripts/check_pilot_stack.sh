#!/usr/bin/env bash
set -euo pipefail
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
export ATLAS_WORKER_PASSWORD="${ATLAS_WORKER_PASSWORD:-$(openssl rand -hex 16)}"
export ATLAS_API_PASSWORD="${ATLAS_API_PASSWORD:-$(openssl rand -hex 16)}"
base=http://127.0.0.1:8080/Air-Atlas-RO/
trap 'docker compose --profile replay down --volumes --remove-orphans' EXIT
check() {
  python3 - "$base" "$1" <<'PY'
import json
import re
import sys
import urllib.request
from pathlib import Path

base, expected = sys.argv[1], sys.argv[2]
query = "api/history/dj1-no2?from=2026-10-10T18:00:00Z&to=2026-10-10T20:00:00Z"
with urllib.request.urlopen(base + query, timeout=10) as response:
    document = json.load(response)
fixture = json.loads(Path("tests/fixtures/pilot_history_response.json").read_text())
hours = [f"{row['observedFrom']}/{row['observedTo']}" for row in fixture["observations"] + fixture["missingIntervals"]]
if expected == "empty":
    assert document["observations"] == [], document
    assert [f"{row['observedFrom']}/{row['observedTo']}" for row in document["missingIntervals"]] == sorted(hours), document
else:
    assert document["observations"] == fixture["observations"], document
    assert document["missingIntervals"] == fixture["missingIntervals"], document
assert [row["code"] for row in document["sources"]] == ["EEA", "RNMCA"], document
with urllib.request.urlopen(base, timeout=10) as response:
    page = response.read().decode()
entry = re.search(r'src="(/Air-Atlas-RO/assets/[^"]+\.js)"', page).group(1)
with urllib.request.urlopen(base.split("/Air-Atlas-RO/")[0] + entry, timeout=10) as response:
    script = response.read().decode()
panel = re.search(r"PilotHistoryPanel-[\w-]+\.js", script).group(0)
with urllib.request.urlopen(base + "assets/" + panel, timeout=10) as response:
    assert "Istoric pilot" in response.read().decode()
print("pilot stack:", expected)
PY
}
docker compose up --build --detach --wait db web
if docker compose port db 5432 2>/dev/null | grep -q .; then
  echo 'The database port must not be published.'
  exit 1
fi
check empty
docker compose --profile replay run --rm worker
check stored
docker compose --profile replay run --rm worker
check stored
docker compose restart db
for attempt in $(seq 1 30); do
  if curl -fsS -o /dev/null "${base}api/health"; then break; fi
  if [ "$attempt" -eq 30 ]; then echo 'History API did not recover after the database restart.'; exit 1; fi
  sleep 2
done
check stored
