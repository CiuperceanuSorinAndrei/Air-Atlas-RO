# Plan and roadmap

This Markdown plan describes the current stage; no PDF is required. Implemented work and planned
work are deliberately separated. Last reviewed: 2026-10-01.

## Existing stage

The public product is an EEA NO2/PM10 static map with source provenance, interval/status display,
partial coverage, a six-hour freshness filter and scheduled snapshot-to-Pages automation.
Manual history selectors/normalization exist. A private, empty multi-source Supabase/PostGIS
foundation exists, with reproducible migrations and integrity/security tests.

The September 30 hardening work adds strict data contracts, download limits, expiring metadata,
regression-safe atomic publication, frontend snapshot validation/polling, accessible information,
licence attribution, dependency repair and publication checks. It does not implement history
collection/persistence or national pollution scoring.

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
implemented Romanian NO2/PM10 feed against registered EEA streams.

Local verification on 2026-10-01: 104 Python tests including real PostgreSQL 18.6/PostGIS 3.6.4
transactions, rights denial, rollback, corrections, invalidation and concurrency pass. A bounded
real EEA probe stored 2,078 accepted rows locally; the second sync returned 304 and preserved row
IDs and ingestion timestamps. Azure's observed unquoted `0x...` ETag is preserved exactly.
The production target and Linux CI remain PostgreSQL 17; those CI checks have not run for this
unpublished checkpoint. No live Supabase rows, credentials or scheduled history ingestion changed.

Next activation gates: review/apply the role migration, register EEA streams with independently
verified licence/storage rights, provision the private login and TLS trust, then demonstrate
backup/restore before real history storage. Publish through required CI before deployment.
The writer is prepared and tested locally; none of these live activation gates is completed.

## Subsequent milestones and gates

1. Add SO2, O3 and PM2.5 only after interval/unit/quality/freshness and coverage review. CO requires
   its own unit contract; current concentration validation must not be copied blindly.
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

## Continuation gate

Before new work, use the recorded release evidence in the hardening review: clean repository,
passing code/data/SQL gates, exact deployed revision, database patch level, private grants/RLS and
no unresolved release-blocking findings. No finite test suite establishes that software can never
have a bug; changes reopen the relevant checks. Unimplemented future milestones are not completed work.
