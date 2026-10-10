# Operations and recovery

## Code release

Run `bash scripts/check.sh` on the intended commit. Publish that commit to a review branch so
GitHub can run `production-checks`, including clean-database migration tests. `main` requires that
successful check from GitHub Actions, linear history, resolved review conversations and prohibits
force pushes/deletion. Fast-forward the same verified SHA to `main` or merge a checked pull request.
Never manufacture a passing check for code that has not completed the gate.

The refresh workflow requires matching approved code assets, then checks its exact data candidate
with importer tests and integrity validation before uploading only the JSON. Its write job revalidates coverage/interval regression against the same
base SHA. A temporary branch makes the candidate SHA available to GitHub; the workflow records
its actual completed gate as `production-checks`, pushes fast-forward to protected `main` and
removes the temporary branch. Failure or a racing main change leaves the published snapshot intact.
The next scheduled/manual run starts from the new main; no force push or automatic rebase is used.

## Import failure or stale page

Inspect `Refresh EEA observations` and the subsequent Pages run separately. Code, data import,
publication and deployment are distinct outcomes. Check public `observations.json`, its maximum
`ingestedAt`, failed/skipped counts and measurement freshness. A successful build alone does not
prove fresh data. The redesigned frontend preserves latest-known readings with visible age even when updates fail; their recent status expires.

Snapshot publication rejects any nonzero `importSummary.failed`, including when only expired
previous readings are absent or no baseline file exists. The collector still returns partial reports
for diagnostics; document validation checks their structure, not publication eligibility. Failures
count attempts, so even a recovered fallback after a failed attempt conservatively blocks publication.
`skipped` means a successfully read series has no valid observations and does not alone block writing.

Missing previous station/pollutant pairs block publication while their last interval end is less than
six hours old. Expired missing pairs may leave the current-map snapshot; this does not delete stored
database history or establish that a provider revoked the series. Common pairs retain the interval
regression guard, and same-interval corrections remain allowed. A regressed pair may publish only
when a fresh read of the same source series explicitly marks the exact previous sampling-point,
interval, aggregation and unit invalid with integer `Validity=-1`. Missing rows, `-99`, ambiguous
matches, changed series and failed verification do not establish invalidation. The candidate must
also match the latest valid source row, including its value, interval, quality and provenance;
only its ingestion timestamp may differ. Every regressed pair must pass, or the entire previous
snapshot remains intact. The collect and publish jobs independently recheck this boundary.
Measurement recency is monitored separately and does not reject a structurally valid latest-known
snapshot. The frontend preserves the original observation time and labels expired data.

Transient network/provider failures may recover on the next run. Schema, identity, unit, coordinate
conflict, recent lost pair or unconfirmed regressed interval errors require source review. Confirmed
source invalidations are logged with the affected pair and previous interval. If EEA changes again
between collection and publication, the candidate is rejected; collect again rather than reusing
an outdated artifact. Never relax a guard simply to turn a workflow green. For a genuine retired
series that does not meet the automatic invalidation contract, save the previous snapshot,
record the official evidence and affected station/pollutant pairs, make a reviewed baseline change
and rerun every gate. Same-interval corrections need no baseline reset.

Workflow-failure emails are owner-reported enabled. Delivery is not independently demonstrated;
verify a controlled failure reaches the chosen inbox before claiming alert coverage. Do not rely
on email or GitHub's hourly schedule as a production freshness SLA.

## External scheduling and monitoring

Checkly now dispatches `refresh-eea.yml` every 60 minutes and checks the public snapshot every
15 minutes, independently of GitHub cron. Both checks rotate between Frankfurt and London,
use no retries, and subscribe to the owner email channel for failure/recovery. The public monitor
requires ingestion strictly younger than two hours and at least one reading strictly younger than
six hours; it does not establish complete national coverage. The Python fallback report now exposes
`ingestion_age_hours` and `ingestion_is_recent` to distinguish source latency from ingestion delay.

The dispatch and public-data checks passed remote test sessions. A controlled scheduled failure
and recovery produced successful Checkly email-send records; inbox receipt is still owner-confirmed
evidence. The public-data check also passed its first automatic production run. Keep GitHub schedules
as fallback until a recurring external dispatch completes import, publication and served-data
verification, then review observed cadence. An interval is not an exact top-of-hour start or an SLA.
See [Checkly configuration and evidence](../ops/checkly/README.md) for source, non-secret settings,
check IDs and verification limits. Checkly credentials remain outside Git and browser assets.

## Production availability and freshness policy

Decision recorded October 6; local workflow implementation added October 8. Code releases retain
the full gate. Data updates reuse approved assets and run data-specific integrity checks. A separate
hourly served-data monitor and daily full security gate are implemented; remote execution and alert
delivery remain unverified. See [release separation](release-separation.md) for exact mechanics and
bootstrap/retention limits. The frontend displays old readings with their age.

A reachable page with expired observations is a degraded air-quality service. Measure page/data
availability separately from measurement freshness and source coverage. Track three distinct
signals: the last attempted run, the last verified snapshot actually served to users, and each
reading's source interval end. Neither a recent download timestamp nor a successful workflow proves
recent measurements. A valid unchanged snapshot remains a valid result; monitor successful checks
and observation age separately rather than requiring a changed payload every hour.

| Failure | Required production behavior |
| --- | --- |
| New release fails tests or dependency audit | Block that release; retain the accepted application version. A build-only advisory must not independently stop valid data refreshes on an approved execution environment. |
| Provider timeout, invalid data or rejected snapshot | Preserve the last accepted snapshot and its actual timestamps; use bounded retries for transient errors, then alert. Never relabel old observations as fresh. |
| Freshness or coverage falls below the accepted target | Keep the page available with explicit degraded status, last accepted update and affected coverage. Keep latest-known readings visibly labelled with age; never imply they measure the selected current hour. Future hourly products distinguish modelled estimates from measurements. |
| Exploitable vulnerability affects the active serving or ingestion component | Assess exposure and isolate, roll back or stop the affected component as needed; document the mitigation. Do not bypass the audit with blanket continue-on-error. |

The initial warning threshold is two missed expected hourly end-to-end update checks. Account for
run duration and observed scheduling delay when implementing it. The existing six-hour recency
window remains a freshness label, not an availability SLA. Choose and validate numerical SLOs
from observed provider cadence, publication latency and covered station/pollutant pairs before
making a national production promise. One recent reading must not hide widespread coverage loss.

An independent monitor must check the served JSON, freshness and coverage even if the ingestion
scheduler never starts. A successful fetch of the same old JSON does not mean the ingestion path
recovered; the current frontend fetch-error message alone does not detect this incident. Verify
alert delivery with controlled failures and send a recovery notice only after served data passes
the same checks. GitHub workflow emails alone are not this monitor.

### Separation of releases and data publication

1. Release code and dependencies through full tests, SQL checks, security audit and build. Record
   the accepted commit and immutable application/ingestion artifacts with provenance and hashes.
2. Run periodic ingestion using the approved code and locked execution environment. Its gate checks
   schema, identity, units, quality, intervals, failed attempts and coverage/regression.
   Measurement freshness is monitored separately.
   Security monitoring remains active separately; new advisories require triage and a patched release.
3. Publish only validated snapshots alongside the exact accepted application assets, atomically.
   Pages must not rebuild or re-audit the application for each data-only update. Verify the code
   revision and asset hashes as well as the snapshot. Keep protected-main rules: a data-specific
   check cannot authorize unrelated code, dependency or schema changes.
4. Measure the user-visible result from an independent scheduler/monitor. Before replacing the
   demo schedule, evaluate a durable scheduler with recorded runs, bounded retry and replay,
   operational ownership and measurable freshness targets. GitHub cron alone has no freshness SLA.
5. Exercise release/audit failure, provider timeout, invalid snapshot, publication failure, missed
   scheduling, stale/partial coverage and recovery. Assert the accepted site survives, bad data never
   replaces valid data, alerts arrive, and approved fresh data can still publish during a blocked release.

The local implementation is ready for remote validation; do not claim the separation is operational
before the initial approved code artifact and a real data-only Pages run are verified. Existing
source-integrity guards remain mandatory. Remote failure/recovery drills and notification delivery
are still pending; [release separation](release-separation.md) records the current evidence.
Sources: [Google SRE data-processing guidance](https://sre.google/workbook/data-processing/)
for freshness/correctness and end-to-end measurement;
[GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
for delayed or dropped scheduled jobs.

## Rollback

Revert the faulty code commit on a review branch, run checks, then merge/push the verified revert.
Do not reset/force-push main. For a bad data snapshot, restore the last accepted JSON in a reviewed
commit, documenting why intentionally older data is being used. Deploy that checked commit and
verify the public JSON/assets. Old data loses recent status; the redesigned latest-known view keeps accepted values visible with age.

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
approved stream sync-field updates, source-device inserts, current device metadata updates and
approved observation replacement. Device updates are restricted to `provider_location`,
`provider_station_name` and `metadata_synced_at`, for an approved EEA Parquet stream.
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
Every approved sync resolves and validates station metadata independently of the Parquet ETag,
using the existing 24-hour cache. Existing source devices receive the accepted name, coordinates
and `metadata_synced_at` in the same transaction as the stream checkpoint. That timestamp records
local synchronization, not a new source fetch or the observation time. HTTP 304 preserves every
historical observation field, including its recorded location and station name. Empty accepted
history does not create a device; an existing device can still receive metadata. Metadata failures
or an update affecting anything other than one expected device reject the transaction. Apply
`20261006152355_eea_station_metadata.sql` before running the updated writer. This remains manual
per-stream synchronization, not scheduled station inventory reconciliation.

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
