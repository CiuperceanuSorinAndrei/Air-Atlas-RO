# Plan and roadmap

This Markdown plan describes the current stage; no PDF is required. Implemented work and planned
work are deliberately separated. Last reviewed: 2026-09-30.

## Existing stage

The public product is an EEA NO2/PM10 static map with source provenance, interval/status display,
partial coverage, a six-hour freshness filter and scheduled snapshot-to-Pages automation.
Manual history selectors/normalization exist. A private, empty multi-source Supabase/PostGIS
foundation exists, with reproducible migrations and integrity/security tests.

The September 30 hardening work adds strict data contracts, download limits, expiring metadata,
regression-safe atomic publication, frontend snapshot validation/polling, accessible information,
licence attribution, dependency repair and publication checks. It does not implement history
collection/persistence or national pollution scoring.

## Next bounded implementation

Owner-written `collect_history`: combine valid normalized rows from selected series while preserving
series/source IDs, aggregation and quality metadata. First specify deduplication, malformed-series
handling and failure/count contracts. Use one bounded real EEA fixture; no scheduled full-history
refresh and no browser-bundled historical dataset.

Then add a private ETag-aware writer: transactionally replace one source series and its cursor/ETag,
retain the last accepted series on any failure and handle `304` without rewriting data. Test source
corrections, revocations, duplicate intervals, retries, rollback and concurrent writers. A failure
must never advance the cursor or publish incomplete history. Verify storage rights before writing;
use a dedicated least-privilege ingestion role, not a browser service-role key.

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
