# Offline DJ-1 / NO2 shared observation pilot

`scripts/dj1_no2_pilot.py` combines the existing RNMCA revision/QA path with bounded EEA
normalization and per-hour selection. It performs no network request or database/publication write.
The map and its EEA snapshot retain their existing behavior.

## Scope and evidence

The only admitted channel is RNMCA location `27064`, measurement `6404090401`, and EEA
`RO/SPO-RO0078A_00008_100`: DJ-1 / NO2, hourly `ug.m-3`. Caller-supplied RNMCA elements must
come from that response envelope; an individual raw element does not identify its channel.
EEA station, series URL, sampling point, pollutant, aggregation and unit must agree.
The explicit UTC window must lie between 2025-01-01T00:00Z and 2026-10-12T08:00Z, the
investigated historical period. This deliberately prevents applying the rule to current/future
exports without renewed source evidence. It is not a nationwide normalization rule.

The October 12 captured EEA Parquet row has value `48.81862`, Start `2026-10-10 20:00`,
End `2026-10-10 21:00` and ResultTime `2026-10-10 23:22:32`, under fixed UTC+1.
The documented RNMCA epoch puts the paired value `48.81861538888885` at 18:00–19:00 UTC.
The October 14 original-XML investigation supports a national winter-offset label applied to
summer-clock values for this channel; winter and summer therefore require different treatment.
The fixture records the captured Parquet checksum/size. It contains the existing importer's
JSON representation, current validated station metadata and an explicit offline replay ingestion
time, not literal Parquet bytes or an original capture ingestion timestamp.

## Time interpretation

`bucharest_utc_candidates(local_time)` accepts a timezone-naive `datetime` label and returns a
chronologically sorted tuple of UTC datetimes: zero for a skipped clock label, one for an ordinary
label, two for a repeated label. Input `fold` does not suppress candidates. It preserves
microseconds, does not modify input, and reports datetime-range overflow as `ValueError`.
Both possible folds are round-tripped through UTC and duplicate ordinary candidates collapse.
Its timezone rules come from the runtime's IANA database via standard-library `ZoneInfo`.
[Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html) describes these fold rules.

For the scoped EEA channel, convert the declared interval end to fixed UTC+2 and retain that
clock label, then interpret it with Europe/Bucharest. This reconstructs the national clock label;
it is not a one-hour UTC shift applied to all dates. Derive the start as one physical hour before
the resolved end on the UTC axis. Do not reinterpret both source endpoints independently.

Original labels, native QA, units and precision remain in detached `rawObservation` input copies.
This EEA copy is the declared observation dictionary before the pilot correction, not decoded
Parquet storage bytes. `reportedAt` and `ingestedAt` remain unchanged. The versioned rule is
`eea-dj1-no2-national-clock-bucharest-v1`. Admitted intervals use explicit UTC offsets and must
be completed at ingestion. Daily/rolling aggregates and other channels are rejected.

## Entry point and output

`reconcile_dj1_no2_window(rnmca_data_elements, eea_observations, *, window_start, window_end,
ingested_at)` receives raw RNMCA revisions and EEA declared-observation dictionaries in the
existing importer shape. EEA dictionaries may also contain the known rejected validity `-1`,
so invalidations can supersede earlier valid copies. An explicit null value on validity -1 is
retained as rejected evidence; accepted nulls, missing value keys and malformed numeric values
remain errors. Unknown QA and malformed data are errors,
including malformed content hidden behind a known rejection. It returns one report or raises;
callers must retain the previous accepted state if processing fails.

The report contains current raw RNMCA revisions, all current EEA normalization results,
excluded EEA observations, selected chronological `observations`, explicit `missingIntervals`
and `latestObservation` (or null). The explicit window retains leading and trailing gaps and cannot end after replay ingestion.
Every supplied EEA revision, including a superseded one, must have been ingested by replay time.
The output is an offline pilot report, not a drop-in replacement for the map snapshot schema.

| EEA temporalStatus | Meaning and selection |
| --- | --- |
| resolved | One evidence-supported canonical hour; eligible for selection. |
| qa-rejected | Known invalidity; retained as evidence, excluded from selection. |
| nonexistent | Source clock end has no real local occurrence; no invented interval. |
| ambiguous | Repeated local end; preserve both candidate intervals and select neither. |
| conflict | RNMCA overlap disagrees, or declared labels match an uncorrected RNMCA hour and suggest export drift; quarantine EEA. |

A single equal value, even within the fixed channel, cannot resolve a repeated local hour.
RNMCA epoch observations remain usable there; EEA needs independent disambiguating evidence
before a future rule may accept that ambiguous label. Raw candidate intervals remain inspectable.
Overlap comparison uses absolute tolerance `1e-5 ug.m-3` for captured source precision, never
relative tolerance or averaging. A detected conflict does not remove the accepted RNMCA row.
Without overlap, fallback is conditional on the scoped historical rule; it is not independent
proof of each EEA hour or detection of every possible upstream semantics change.

## Revisions and source selection

RNMCA reconciliation occurs before QA, with its existing highest-version/withdrawal contract.
Retain raw rejected revisions when replaying successive captures; accepted observations alone
cannot preserve withdrawal state. Absence in another capture does not imply a withdrawal.

EEA revisions group by original declared interval. Newer `reportedAt` wins within that group;
equal-report conflicting content is an error, including rejected rows. Exact content duplicates
collapse; ingestion time alone is not a source revision, and the later ingestion copy is retained
deterministically. This conservative ordering is a bounded replay policy, not a provider cursor
or proof that every real correction necessarily advances ResultTime.

At each canonical hour select eligible RNMCA first, otherwise resolved/eligible EEA. Retain
source-specific provenance and values; do not average duplicate provider copies. An EEA row
may fill an hour rejected or absent in RNMCA only if EEA independently passes its own checks.
Select the latest observation after per-hour reconciliation, so an older RNMCA reading cannot
hide a genuinely newer EEA hour. Empty, invalid and ambiguous hours remain explicit gaps.

## Verification and limits

Tests cover the source pair, zero, units/identity, malformed QA/value/provenance, revisions,
withdrawals/replay, overlap conflicts, export drift, winter/summer, spring gaps, autumn ambiguity,
23/25-hour windows, completed intervals, precise timestamps and detached inputs. A calendar
property check reconstructs every physical UTC hour in 2025 and 2026 (17,520 instants).
Synthetic cases test policy; they are not extra provider captures or evidence of real revisions.

The full October 12 offline capture replay uses 179 RNMCA NO2 elements and 142 EEA rows in the
same 179-hour requested window. It selects 172 RNMCA observations, preserves seven QA gaps,
resolves 136 EEA rows and excludes six EEA invalidations. It adds no real EEA fallback hours.
Reversed/duplicated input replay is identical and original inputs remain unchanged. Removing
one RNMCA row synthetically gives 171 RNMCA + one EEA with the same seven remaining gaps;
this demonstrates fallback without claiming the historical RNMCA row was actually absent.

Run `pytest tests/test_observation_time.py tests/test_dj1_no2_pilot.py tests/test_pilot_closure.py`
and the existing RNMCA suites. Closure regressions also verify that a rejected single RNMCA
hour still validates source version, interval and extrema before returning no observation.
The [bounded persistence writer](dj1-no2-history.md) now stores this report and selected
observations atomically, including normalization versions and rejected raw evidence.
The complete API/frontend/Compose pilot, changed-rule reprocessing, live overlap monitoring,
source expansion and operational recovery remain later integration work.
