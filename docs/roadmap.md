# Plan and roadmap

This Markdown plan describes the current stage; no PDF is required. Implemented work and planned
work are deliberately separated. Last reviewed: 2026-10-02.

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
replacement. Rows and ETag commit together. A matching conditional GET updates only the successful
check time; failures leave the accepted checkpoint intact. A complete valid-schema series with all
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

Today's review/publication and manual pilot activation are complete. Future history rollout must
review inventory, bounded source failures, metadata-only refresh and durable backup ownership
before scheduled national ingestion. The snapshot cron is unchanged.

Local six-pollutant verification on 2026-10-02: 755 readings at 200 stations, zero failed imports;
all 351 previous NO2/PM10 pairs preserved without interval regression. CO source values retain
`mg.m-3`; the other five retain `ug.m-3`. 111 Python tests (including actual disposable PostgreSQL
integration), 22 frontend tests, lint, snapshot validation and build pass. The full collection took
about 13.5 minutes within the existing 30-minute import budget; this timing is not a provider SLA.

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
| Metadata-only refresh | Before treating stored station context as continuously synchronized | Changed station metadata is reconciled even when measurement ETag is unchanged; stale/conflicting metadata is handled visibly |
| History inventory rollout | Before recurring multi-series ingestion | Reviewed series/rights inventory, representative correction/withdrawal/timeout/conflict cases, measured resource limits and partial-failure behavior |
| Automated off-site backups | Before recurring production history | Defined retention and owner; automatic backups outside this machine; isolated restore verifies observations, decimals, geography, timestamps and identity sequences |
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

## Continuation gate

Before new work, use the recorded release evidence in the hardening review: clean repository,
passing code/data/SQL gates, exact deployed revision, database patch level, private grants/RLS and
no unresolved release-blocking findings. No finite test suite establishes that software can never
have a bug; changes reopen the relevant checks. Unimplemented future milestones are not completed work.
