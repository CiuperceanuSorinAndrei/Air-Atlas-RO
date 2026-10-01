# Operations and recovery

## Code release

Run `bash scripts/check.sh` on the intended commit. Publish that commit to a review branch so
GitHub can run `production-checks`, including clean-database migration tests. `main` requires that
successful check from GitHub Actions, linear history, resolved review conversations and prohibits
force pushes/deletion. Fast-forward the same verified SHA to `main` or merge a checked pull request.
Never manufacture a passing check for code that has not completed the gate.

The refresh workflow checks its exact candidate, including SQL migrations and freshness, then
uploads only the JSON. Its write job revalidates coverage/interval regression against the same
base SHA. A temporary branch makes the candidate SHA available to GitHub; the workflow records
its actual completed gate as `production-checks`, pushes fast-forward to protected `main` and
removes the temporary branch. Failure or a racing main change leaves the published snapshot intact.
The next scheduled/manual run starts from the new main; no force push or automatic rebase is used.

## Import failure or stale page

Inspect `Refresh EEA observations` and the subsequent Pages run separately. Code, data import,
publication and deployment are distinct outcomes. Check public `observations.json`, its maximum
`ingestedAt`, failed/skipped counts and measurement freshness. A successful build alone does not
prove fresh data. The frontend expires old readings even when updates fail.

Transient network/provider failures may recover on the next run. Schema, identity, unit, coordinate
conflict, lost pair or regressed interval errors require source review. Never relax a guard simply
to turn a workflow green. For a genuine retired/revoked series, save the previous snapshot, record
the official evidence and affected station/pollutant pairs, make a reviewed baseline change and
rerun every gate. Same-interval corrections need no baseline reset.

Workflow-failure emails are owner-reported enabled. Delivery is not independently demonstrated;
verify a controlled failure reaches the chosen inbox before claiming alert coverage. Do not rely
on email or GitHub's hourly schedule as a production freshness SLA.

## Rollback

Revert the faulty code commit on a review branch, run checks, then merge/push the verified revert.
Do not reset/force-push main. For a bad data snapshot, restore the last accepted JSON in a reviewed
commit, documenting why intentionally older data is being used. Deploy that checked commit and
verify the public JSON/assets. Old data may correctly disappear from the recent map.

Database migrations are applied separately from code deployment. Tables are private; the October 1 EEA pilot stores history. The repository migrations reproduce the foundation; CI tests rebuild it from zero.
Before storing real history, establish backup retention and restore drills, then use additive,
reviewed migrations. Never point the disposable CI database script at production. Any future
writer must update rows and its ETag/cursor in one transaction and leave both unchanged on failure.

## Security and provider limits

No database credentials or privileged keys belong in Vite/browser assets. The `atlas` schema has
RLS and no public policies or application-role grants. Satellite assets remain separate from
surface observations. Public display requires verified rights, storage permission and attribution.

The HTML CSP restricts scripts/connections to the same origin, images to local/OSM and blocks
objects/base injection. Inline styles are allowed for Leaflet positioning. GitHub Pages controls
HTTP headers; custom `X-Content-Type-Options` and CSP response headers require another host.
Review each dependency/action/image update through the same checks; pinned versions are not a
substitute for security updates. Review the Supabase database patch level after provider upgrades.


## Manual history synchronization

`scripts/sync_eea_history.py` uses direct PostgreSQL transactions rather than separate REST writes.
The `atlas_ingestor` migration creates a NOLOGIN role with RLS and grants only EEA source reads,
approved stream sync-field updates, source-device inserts and approved observation replacement.
It cannot change licensing, register sources/streams or access canonical sites/gridded assets.
Provision a private login separately only after the activation gates in the roadmap are reviewed.
Keep its DSN in `ATLAS_INGESTION_DSN` outside version control and frontend configuration; supply
the appropriate server CA via libpq TLS configuration. The CLI enforces `sslmode=verify-full`.

Run selected approved streams manually:

```bash
uv run --locked python scripts/sync_eea_history.py --stream-id 123 --stream-id 456
```

Each stream has its own transaction. A failure leaves that stream's observations, ETag and
successful-check time intact; other completed streams can remain committed and the process exits
nonzero. HTTP 304 preserves observation IDs and ingestion times but advances `last_synced_at`.
HTTP 200 replaces the complete accepted series, including old corrections and revocations.
HTTP 206, missing/malformed ETags, malformed identity/schema and conflicting duplicate intervals
are rejected. An empty valid-schema source version is accepted as empty history; failed fetches
are never interpreted as revocations. Database lock waits and SQL execution are bounded.

New/missing series are reported from discovery. Review missing series against the source before
removing any stored data. Review and register newly discovered series with verified rights before
including their IDs in a run. History sync is not wired into the snapshot workflow or browser.
Station metadata is validated on changed-series import; metadata-only changes require their own
refresh plan before treating the stored station context as continuously synchronized.

Integration tests run only against a disposable local `atlas_audit` PostgreSQL database via
`ATLAS_TEST_DATABASE_DSN`. CI creates that database and exports its DSN before the Python gate.
Without that variable, database integration tests are explicitly skipped; HTTP/unit tests still run.
Never set this test variable to a live database.

### Activated pilot and private local configuration

The October 1 pilot uses the Session pooler (port 5432), with a dedicated `atlas_ingestor` login,
connection limit 2, no administrator privileges and no RLS bypass. Its CA and credentials are in
`~/Library/Application Support/Atlasul Aerului/`, outside Git; the directory is mode 0700 and
credential/backup files are mode 0600. Never paste the credential JSON or DSN into an issue/log.
The deployment migration deliberately retains NOLOGIN until separately provisioned.

On this configured machine, run the selected pilot using the private configuration:

```bash
.venv/bin/python -c 'import json, os; from pathlib import Path; from psycopg.conninfo import make_conninfo; config = json.loads((Path.home() / "Library/Application Support/Atlasul Aerului/ingestion.json").read_text()); os.environ["ATLAS_INGESTION_DSN"] = make_conninfo(**config); os.execv(".venv/bin/python", [".venv/bin/python", "scripts/sync_eea_history.py", "--stream-id", "3"])'
```

Verified live result: 2,080 accepted rows, followed by 304 preserving IDs and ingestion times.
The private populated JSON backup restored all six table payloads exactly into local PostgreSQL
after normalizing the session time zone to UTC, including decimal values and PostGIS coordinates;
identity sequences were advanced after restore. A fresh schema comes from repository migrations.
The two other private tables were independently verified empty when this pilot backup was taken.
This local backup/drill is not automated off-site retention; establish that before recurring
production history. Recheck recovery after schema or data-volume changes.

The configured CA is `~/Library/Application Support/Atlasul Aerului/supabase-ca.crt`.
It is a verified copy of the downloaded certificate; the redundant Downloads copy was removed.
The writer reads the private path, so cleaning Downloads does not affect TLS verification.
