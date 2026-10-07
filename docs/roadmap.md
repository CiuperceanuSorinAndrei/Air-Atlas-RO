# Plan and roadmap

This Markdown plan describes the current stage; no PDF is required. Implemented work and planned
work are deliberately separated. Last reviewed: 2026-10-06.

## Existing stage

The snapshot pipeline supports EEA NO2, PM10, PM2.5, SO2, O3 and CO with source provenance,
interval/status display, partial coverage, a six-hour freshness filter and snapshot-to-Pages automation.
Manual history selectors/normalization exist. A private multi-source Supabase/PostGIS
foundation exists, with reproducible migrations and integrity/security tests.

The September 30 hardening work adds strict data contracts, download limits, expiring metadata,
regression-safe atomic publication, frontend snapshot validation/polling, accessible information,
licence attribution, dependency repair and publication checks. The October 1 work separately adds history
collection and a manual persistence pilot; national pollution scoring remains unimplemented.

## History collection and next bounded implementation

`collect_history` now combines manually selected series without station/pollutant fallback.
It retains source/series IDs, intervals, aggregation and quality. Duplicate URLs are fetched once;
identical interval rows are collapsed ignoring only `ingestedAt`. Conflicting rows reject the
entire affected series; successful other series remain available for diagnostics. Empty valid
history is skipped; malformed series and download/validation failures are failed.
`attempted`, `imported`, `skipped`, `failed` count unique requested URLs/series;
`observationCount` and `duplicateCount` count retained rows and collapsed duplicates in successful
series. An empty input returns an empty report. This report is not a latest-map snapshot and must
not be passed to `validate_document` or `write_observation_snapshot`. Partial reports are not
permission to replace durable history. No scheduled full-history refresh or browser history bundle.

`scripts/sync_eea_history.py` now prepares manual PostgreSQL ingestion using a dedicated
`atlas_ingestor` role. A locked stream row serializes concurrent writers; verified storage rights,
source/series identity, bounded full HTTP 200/Parquet decoding, quality and duplicate checks precede
replacement. Rows, current source-device metadata and ETag commit together. A matching conditional
GET preserves observations while resolving station metadata through the 24-hour cache and updating
the successful-check time; failures leave the accepted checkpoint intact. A complete valid-schema series with all
rows invalidated can replace accepted history with an empty set. Missing series in discovery are
reported for review and are not automatically deleted. Inventory reporting compares the currently
implemented Romanian six-pollutant feed against registered EEA streams.

Local verification on 2026-10-01: 104 Python tests including real PostgreSQL 18.6/PostGIS 3.6.4
transactions, rights denial, rollback, corrections, invalidation and concurrency pass. A bounded
real EEA probe stored 2,078 accepted rows locally; the second sync returned 304 and preserved row
IDs and ingestion timestamps. Azure's observed unquoted `0x...` ETag is preserved exactly.
PostgreSQL 17/Linux CI passed; PR #10 merged as `1b6bdf8` and Pages deployment succeeded.

Live pilot activation is verified in Air-Atlas-RO: scoped role/RLS applied, EEA dataset licence and
attribution registered, private credentials stored outside Git, session-pooler TLS verified with
the downloaded Supabase CA. The pilot stored 2,080 observations and its second sync returned 304
without replacing IDs or ingestion timestamps. Empty baseline and populated backups were restored
in separate local test storage; all six populated-table payloads matched after UTC normalization.
Backups currently remain private on the owner's machine, without automated off-site retention.

The October 1 review/publication and manual pilot activation are complete. The October 6 change
adds current device name/location synchronization independently of the measurement ETag, with
scoped grants/RLS and complete-metadata constraints. Live schema/grants are verified; the updated
live writer has not yet been exercised. Future history rollout must review inventory, bounded
source failures, scheduled metadata reconciliation and durable backup ownership before recurring
national ingestion. The snapshot cron is unchanged.

Local six-pollutant verification on 2026-10-02: 755 readings at 200 stations, zero failed imports;
all 351 previous NO2/PM10 pairs preserved without interval regression. CO source values retain
`mg.m-3`; the other five retain `ug.m-3`. 111 Python tests (including actual disposable PostgreSQL
integration), 22 frontend tests, lint, snapshot validation and build pass. The full collection took
about 13.5 minutes within the existing 30-minute import budget; this timing is not a provider SLA.

## October 3 snapshot refresh repair

The six-pollutant baseline contained 115 historical pairs absent from the current E2a import:
93 absent from discovery and 22 series without valid rows. Those prior intervals ended no later
than January 1, 2026. Requiring every historical pair blocked otherwise fresh snapshot updates.
The owner wrote the age-based missing-pair classification and common-pair regression selection
with assistant guidance. The assistant wrote the delegated tests and failed-attempt publication guard.

Missing pairs now block only while the previous observation is less than six hours old; common
pairs retain the regression guard, with the confirmed source-invalidation exception documented
below. Any failed import attempt blocks publication, while successfully read
series without valid observations remain skipped. This is current-map snapshot retention, not
database history deletion or proof of provider revocation. The publication guard does not establish
discovery completeness, national coverage or a provider SLA.

Bounded live verification: 717 attempts, 640 readings at 180 stations, 77 skipped, zero failures;
629 readings were recent at collection verification. The same 115 historical pairs were omitted,
with no recent missing pair or common-pair interval regression. Freshness is evaluated again at
release; historical counts are evidence, not a promise of ongoing availability.

## October 7 source invalidation repair

A read-only comparison of 717 discovered groups reproduced three regressions against the served
snapshot: OT-1 (`RO0174A`) CO and CL-3 (`RO0213A`) PM10/PM2.5. The exact previously published
intervals now have source `Validity=-1`, previously `1`; selecting the latest valid row therefore
moves backward. An unconditional monotonicity check blocked fresh data for unrelated stations.

The assistant implemented the explicitly delegated repair and regression tests. Publication now
requires a fresh same-series confirmation of the previous interval's invalidation and a complete
match between the candidate and the current latest valid source row. Missing/ambiguous evidence,
quality/value/provenance mismatches and failed reads still preserve the complete prior snapshot.
This recheck runs at both collection and publication; stale replacements remain excluded by the
frontend's existing six-hour rule. No history data or SQL schema is changed.

Tests cover confirmed, unconfirmed and mixed regressions, wrong identities/units/aggregation,
malformed evidence, changed source series, candidate mismatches, timezone equivalence and failed
verification. Changed-history HTTP 200 tests additionally check existing-device metadata refresh,
revocation and rollback of observations, ETag, checkpoint and metadata on failure. Verification
and deployment results must be recorded separately; test coverage alone does not prove recovery.

## Subsequent milestones and gates

1. SO2, O3, PM2.5 and CO are supported by the snapshot importer and frontend. Preserve source
   units: CO in `mg.m-3`, all other supported pollutants in `ug.m-3`. Interval, quality and six-hour
   freshness rules apply independently to each reading; no national completeness is implied.
2. Preserve station classification and scoring windows. Implement European AQI from its official
   method only after temporal completeness and QA rules are tested. US AQI is a separate method.
   Do not derive a national score from one latest NO2/PM10 reading.
3. Add legally permitted original Romanian providers after access, licence, attribution, inventory,
   authority tier and overlap review. RNMCA credentials and supplier permissions remain pending.
4. Add CAMS model layers and Sentinel-5P column context as separate domains. Satellite columns are
   not surface concentrations; any conversion requires independent validation and uncertainty.
5. National release requires a durable scheduler, measured freshness/availability targets,
   independently tested alert delivery, backup/restore drills after data is stored, operational
   ownership and a supported tile-hosting plan. GitHub cron and public OSM tiles remain demo choices.

## Operational work already planned

| Work | When it is required | Completion evidence |
| --- | --- | --- |
| Metadata-only refresh | Manual per-stream path implemented; verify live behavior and inventory-wide scheduling before continuous-sync claims | HTTP 304 preserves all historical observation fields while current device metadata updates; failures roll back; source fetch remains subject to the 24-hour cache |
| History inventory rollout | Before recurring multi-series ingestion | Reviewed series/rights inventory, representative correction/withdrawal/timeout/conflict cases, measured resource limits and partial-failure behavior |
| Automated off-site backups | Before recurring production history | Defined retention and owner; automatic backups outside this machine; isolated restore verifies observations, decimals, geography, timestamps and identity sequences |
| Release/data publication separation | Before a release-only failure can be isolated from periodic data delivery | Approved immutable code/assets, a data-specific gate, atomic publication and tests proving blocked releases do not prevent safe data updates; see operations policy |
| Scheduler and freshness/availability targets | Before claiming a national production service | Agreed targets, measured runs, bounded retries and explicit stale-data behavior; external tile service has a supported hosting plan |
| Monitoring and alert delivery | Before unattended production operation | Deliberately failed import, stale snapshot and failed backup trigger independently verified notifications and a usable recovery runbook |
| Dependency maintenance | At each update and before release | Reviewed compatibility, locked packages/pinned actions, passing full CI and deployed-workflow checks |

Do not promise universal correctness: release evidence applies to the implemented scope and tested
failure cases. Reopen relevant checks when code, schema, provider contract or deployment changes.

October 1 dependency review accepts Ruff 0.16.9, ESLint 10.11.0, React Refresh plugin 0.5.7,
Vite 8.3.1 and the pinned cache/upload/download actions. TypeScript 7 is deferred until
typescript-eslint officially supports it and full CI passes; current supported range excludes 7.
Node 26 types are deferred until the runtime itself is intentionally upgraded from Node 24.
Dependabot major updates for these two packages are ignored meanwhile; reconsider the ignore
entries when their gates are met. Minor/security updates remain eligible for review.
Source: [typescript-eslint dependency support](https://typescript-eslint.io/users/dependency-versions/).

## Production reliability decision — October 6

The accepted target is separate page availability, measurement freshness and source coverage.
A release failure retains the accepted version; an import failure retains the accepted snapshot;
expired observations produce visibly degraded service. Independent monitoring must detect missed
runs and stale served data. The initial warning proposal is two missed hourly update checks;
measured provider/publication latency must determine final SLOs.

Release/data publication separation and these monitoring/recovery drills remain unimplemented.
Both current refresh and Pages workflows still run the full audit gate. The bounded October 6 PR
repairs source-map-js and adds manual device metadata synchronization; it documents the reliability
policy without changing the workflows. The implementation sequence and acceptance scenarios are
in [operations](operations.md#production-availability-and-freshness-policy).

## Continuation gate

Before new work, use the recorded release evidence in the hardening review: clean repository,
passing code/data/SQL gates, exact deployed revision, database patch level, private grants/RLS and
no unresolved release-blocking findings. No finite test suite establishes that software can never
have a bug; changes reopen the relevant checks. Unimplemented future milestones are not completed work.
