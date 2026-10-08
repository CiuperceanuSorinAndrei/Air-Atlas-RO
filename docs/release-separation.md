# Code releases, data publication and served-data monitoring

October 8 release candidate: implemented and verified locally; owner has authorized publication.
Remote CI, code deployment and the first data-only workflow exercise are pending at this checkpoint. The Geoapify Actions repository variable is configured. Domain restrictions remain
owner/provider configuration, not independently verified.

## Code release

Production checks keep the full Python, database, frontend, dependency-audit and build gate.
Pages code releases on main repeat that gate, require the Geoapify public browser variable, and
upload both the Pages bundle and a retained `approved-site-<fingerprint>` artifact. The fingerprint
covers every tracked Git-tree entry except `src/data/observations.json`, including workflow,
importer, lockfile and schema changes. The artifact becomes eligible for reuse only after that main
code-release workflow finishes successfully, including deployment. No private credentials enter it.

The public key is provided through the repository Actions variable `VITE_GEOAPIFY_API_KEY`.
Local `.env.local` is ignored; the value is never added to tracked source or documentation.

## Data publication

Refresh requires a retained artifact matching the unchanged code tree from a successful main
code-release workflow. It runs the locked importer, importer tests and schema validation without
Node, npm audit, frontend build or database initialization. Publication rechecks approval and the
existing failed-import, pair-preservation, interval-regression and exact-invalidation boundaries.
Only the snapshot path may be staged. A real successful data-specific gate plus inherited code
approval records the required `production-checks` result; it does not claim full code tests were
rerun. Racing main changes fail the existing fast-forward push. No branch protection is relaxed.

A successful refresh triggers the Pages data job. It downloads the exact approved application
artifact, validates the accepted snapshot and replaces only `observations.json`. It uploads and
atomically deploys the complete Pages bundle without rebuilding or auditing the frontend.
Code release and data deployment share the existing Pages concurrency group.

Measurement age no longer rejects an otherwise valid data publication. Latest-known readings keep
their actual source intervals and the UI explicitly labels old data. All source-integrity guards
remain in force. Empty, invalid or failed imports still cannot replace the accepted snapshot.

## Independent checks and limits

The monitor runs at minute 47 hourly, separately from ingestion, and fetches the publicly served
JSON with bounded reading and timeout. It validates structure/units/source identities, rejects failed
imports, and reports recent/total readings and stations plus per-pollutant recent counts. It fails
when no reading is recent within six hours or the newest served ingestion is at least two hours old.
These are operational warning thresholds, not a national freshness SLA. Zero recent counts remain
visible for individual pollutants; calibrated partial-coverage thresholds are still future work.

Production checks also run daily, retaining independent dependency-audit visibility. This monitor
uses a separate GitHub schedule, not an independent hosting provider: GitHub outages can affect both
schedules. Notification delivery, recovery notices and external monitoring remain unverified.

Approved-site retention is 90 days, subject to repository settings. If the matching artifact is
missing, expired or from an unapproved run, updates fail closed and the served site remains intact.
Renew with an approved full code deployment before expiry. A new code tree must first finish its
code deployment; an intervening refresh may fail until that approval exists. This is a deliberate
bootstrap gate, not permission to reuse a different code version.

## Verification and publication gate

- Full local gate: 158 Python tests passed; 15 PostgreSQL tests skipped because no disposable
  database DSN was configured. All 31 frontend tests, lint, snapshot validation, audit and build pass.
- Nine release-boundary tests cover snapshot-only fingerprint inheritance, changed code rejection,
  foreign/failed/pending workflow provenance, artifact expiration, measurement/import-age separation
  and invalid served data. Actionlint validates all workflows.
- Live monitor probe found 640 readings at 180 stations, zero recent readings and last accepted
  ingestion October 7 at 09:31 UTC. The expected degraded result is distinct from a fetch failure.
- Before declaring the release live: run remote checks including
  clean-database migrations, deploy the initial approved code artifact, exercise a real refresh and
  data-only Pages deployment, compare served assets/data, and verify provider key restrictions.
  Controlled remote audit-failure/provider-timeout/publication-race and notification/recovery drills
  remain unexecuted. Local tests and workflow validation do not substitute for those checks.
