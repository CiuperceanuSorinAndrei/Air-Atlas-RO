# Bounded DJ-1 / NO2 persistence

`scripts.sync_dj1_no2_history.sync_dj1_no2_history` connects the
[offline source contract](dj1-no2-pilot.md) to PostgreSQL/PostGIS. It takes the
same raw RNMCA revisions, declared EEA observations, explicit UTC window and
replay ingestion time. It performs no provider download, public-source switch,
scheduling, API request or frontend update.

## Database and access

Apply all repository SQL migrations before using the updated history writer.
The new migration adds private `atlas.pilot_reports`, an optional
`observations.pilot_report_id` foreign key, and the `atlas_pilot_ingestor`
NOLOGIN role. A separately managed login must explicitly assume this role.
The connection must be idle with autocommit enabled; superuser and RLS-bypass
roles are rejected. No credentials are checked into the repository.

Configure two `source_streams` with format `pilot-replay`, verified storage
permission and existing devices mapped to the same canonical site:

| Source | External stream/device ID |
| --- | --- |
| EEA | `RO/SPO-RO0078A_00008_100` |
| RNMCA | `27064/6404090401` |

Source rights must be verified for actual storage and deployment. Fixture flags
used in tests are synthetic configuration, not legal evidence. Public display
is not enabled by the migration. The role cannot provision sources/devices or
modify the older Parquet history; the old ingestion role cannot access the
pilot's observations. Public roles retain no access to the private schema.

The migration widens observation values from `numeric(38,18)` to unconstrained
`numeric`, preserving finite source decimals without rounding small values or
rejecting large finite ones. Database constraints still reject NaN and both
infinities. Existing values are not recalculated; already rounded historical
data requires explicit source replay.

## Transaction and replay contract

The worker uses READ COMMITTED, a channel-specific transaction advisory lock,
5-second lock timeout, 30-second statement timeout and 5-minute idle transaction
timeout. It reads prior overlapping reports, merges retained native revisions,
then invokes the existing normalizer. Absence is not withdrawal. Native version
and reporting-time rules still determine which revisions supersede others.
Unresolved spring-clock evidence is retained without inventing a UTC interval.
Replay ingestion time cannot move backwards across overlapping reports.

In one transaction it saves the complete report, deletes the selected pilot
observations inside the requested UTC window and inserts the newly selected
observations using the insertion function shared with the EEA history writer.
Rows outside that window and older Parquet streams are preserved. Failures roll
back the report, deletions and inserts together. Rejected/ambiguous observations
stay in the report and cannot become valid measurements through persistence.

`save_report` deduplicates the complete PostgreSQL JSONB serialization using a
generated SHA-256 key and unique constraint. Exact replays return the original
report ID and saved timestamp. Dictionary key order does not create duplicates;
different evidence, normalization version or ingestion metadata may create a
new report. A hash conflict or concurrent removal errors instead of silently
discarding evidence. The helper joins its caller's transaction without committing.
Observation row IDs may change when a window is replaced; report identity and
the selected source/interval/value are the replay contract.

The report retains raw inputs, rejected revisions, original source labels and
normalization versions. Accepted rows reference their report and carry the
normalization rule and native QA metadata. EEA observation coordinates and URL
come from the captured observation; RNMCA uses the configured device location
and source URL because its raw element does not contain them. RNMCA reporting
time remains null when unknown. Values use `Decimal(str(value))` without unit
conversion. Persisted times are timezone-aware instants; set session timezone
to UTC for canonical text readback.

## Verification and recovery

Use a fresh disposable PostgreSQL/PostGIS cluster. `scripts/check_database.sh`
defaults to local port 5432 and user `postgres`; it respects explicit `PGHOST`,
`PGPORT` and `PGUSER` for an isolated local socket. It creates test roles and the
`atlas_audit` database, applies every migration and runs the foundation SQL checks.
Set `ATLAS_TEST_DATABASE_DSN` to that database before running `scripts/check.sh`.
Database tests reject nonlocal targets and database names other than `atlas_audit`.

Tests cover committed readback, exact replay, changed evidence, concurrent
writers, numeric precision/finiteness, role denial, report references, canonical
site mismatch, overlapping windows, corrections, withdrawals, stale/empty
captures, retained EEA fallback and rollback after a write failure. Source QA,
normalization, DST and historical bounds remain covered by the existing suites.

The schema change is additive except for lossless numeric widening and tighter
role policies. Deploy migration before the updated writer. Stop the pilot writer
to roll back code; keep reports and observation links for recovery. Do not narrow
numeric back to `(38,18)` without checking every stored value for exact fit.
The old EEA writer remains compatible with the widened column and optional link.
Restore into isolated storage and compare table payloads before any live rollout.

This is bounded historical persistence, not national recurring ingestion. It
retains all overlapping report snapshots and has no retention/compaction policy.
Changed-rule reprocessing, live source permissions, backup automation and the
bounded read API/frontend/Compose pilot remain separate integration gates.

The [accepted October 28 contract](reliability-and-history-contract.md) replaces this bounded
full-report replay model as the target for recurring ingestion: separate source evidence, semantic
state and execution logs. The current writer still intentionally deduplicates only exact audit
payloads. A later replay context preserves selected measurements but creates a distinct report;
this limit is regression-tested and must not be described as semantic deduplication.
