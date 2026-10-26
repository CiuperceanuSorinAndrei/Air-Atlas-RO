# Local RNMCA DJ-1 / NO2 pilot

This is an offline, bounded hourly normalization and reconciliation path. The separate
[shared DJ-1/NO2 pilot](dj1-no2-pilot.md) now adds scoped EEA alignment and per-hour selection.
Neither path fetches RNMCA, writes history or publishes to the map. The public EEA snapshot
remains on its existing path.

## Source contract and scope

The pilot accepts the captured location `27064` (`DJ-1`) and measurement value `6404090401` only.
The configuration captured on October 12, 2026, version `1791476315175`, maps this channel to hourly
NO2 in micrograms per cubic metre and status group `95002`. Callers must pass IDs taken from the
response envelope and location request, not infer channel identity from a numeric reading.

`tests/fixtures/rnmca_dj1_no2.json` contains a sanitized extract of the captured observation and
configuration. `sourceFiles` records checksums and sizes of the original archived files; these are
provenance references, not a claim that the extracted fixture is byte-identical to those files.
The fixture contains no credentials. Its observation is historical evidence, not a current reading.

The source contracts were read from the captured RNMCA Main Measurement Data API OpenAPI and
configuration. The original documentation endpoint is
<https://calitateaer.ro:8443/api-docs/Main%20Measurement%20Data%20API>.

## Eligibility

`rnmca_hourly_is_eligible(data_element)` returns a boolean for quality and value eligibility:

- Only `Webs_FieldsHourlyValue` is eligible; other known aggregation types return `False`. Unknown or malformed types are errors.
- `usable` must be an actual boolean and must be `True` for acceptance.
- Known QA states are `78001` raw, `78002` operator-validated and `78003` certified. All three can
  be eligible; acceptance does not promote raw data to validated or certified.
- QA status must be `79001` correct. `79002` incorrect and `79003` uncertain return `False`.
- Status flags use the captured channel's group `95002`. A known invalidating flag returns `False`,
  even if the row says `usable=True`. Known non-invalidating flags are retained and may pass.
- The selected value must be available and finite. A zero override is available; a null override
  is unavailable, even if the ordinary value is numeric.

Hourly effective-value structure and numeric validity are checked even when known QA rejects a row.
The hourly row adapter also validates revision, interval and extrema metadata before returning
a known rejection; known non-hourly aggregations still return no observation.
Unknown QA IDs and unknown status bits raise `ValueError`. Malformed boolean, integer or object
types raise `TypeError`. An incomplete override raises `ValueError`. The fixed masks and QA IDs
belong to this captured pilot contract; expanding channels or adopting a changed provider
configuration requires checking that configuration and changing the contract deliberately.

This predicate does not establish interval eligibility or validate an entire HTTP response.

## Local normalized observation

`normalize_rnmca_observation(data_element, *, location_id, measurement_value_id, ingested_at)`
returns an observation dictionary, or `None` for a known quality rejection or unavailable value.
Unknown/malformed inputs are errors rather than accepted observations.

- `ingested_at` is a caller-supplied timezone-aware `datetime`, normalized to UTC. For an offline
  replay it means replay ingestion time, not the original capture time or first provider report.
- The raw epoch in milliseconds is the hourly interval end. The start is one physical hour earlier
  on the UTC axis. Both output times include `+00:00`. Integer millisecond arithmetic avoids float rounding;
  intervals outside the representable datetime range raise `ValueError`.
- This pilot accepts completed intervals: `observedTo <= ingestedAt`. This is separate from the
  existing map's ability to display in-progress intervals.
- A nonnegative source version is required. Available min/max timestamps must fall within the
  source interval, including its endpoints; missing extrema remain missing in the raw copy.
- The output retains source/location/channel IDs, version, pollutant, unit, QA stage and native
  QA/status codes. `stationCode` is an RNMCA code, not an EEA station ID or a permanent device alias.
- `reportedAt` is `None`: the captured row supplies no independent reporting timestamp.
- `normalizationRule` is `rnmca-dj1-no2-hourly-epoch-utc-v1`, and `temporalStatus` is `resolved`
  under that documented epoch contract. This does not prove completeness of the source series.
- `rawObservation` is a detached deep copy of the original element. The function does not change
  its argument or shift any source field. It does not invent an EEA normalization rule.

For the captured row, the result is `48.81861538888885 ug.m-3` over
`2026-10-10T18:00:00+00:00` to `2026-10-10T19:00:00+00:00`.
UTC intervals stay distinct through daylight-saving transitions. Missing source hours are not
filled by this single-row adapter. Source-window checks, duplicate/correction reconciliation,
EEA fallback is implemented in the separate [shared pilot](dj1-no2-pilot.md); persistence,
API and frontend integration remain separate work.

## Batch normalization

`normalize_rnmca_observations(data_elements, *, location_id, measurement_value_id, ingested_at)`
applies the single-row adapter to a list for the captured DJ-1 / NO2 channel. It returns accepted
observations in input order, preserving duplicates and zero values, without changing the input.
Known quality rejections and unavailable values are omitted. Empty or entirely rejected input
returns an empty list. Row errors propagate; no partial list is returned on failure.
This wrapper does not read files, detect missing hours or reconcile source versions. Shared
arguments are validated by the single-row adapter when a row is processed.

## Duplicate and correction reconciliation

`reconcile_rnmca_data_elements(data_elements, *, location_id, measurement_value_id, ingested_at)`
selects one raw hourly element per interval for the fixed pilot channel, ordered by interval.
The archived Main Measurement Data API versioned-data description says operations increment the
version of each affected element; the captured channel is versioned. The highest source version
wins within each interval, independently of arrival order. Equal canonical JSON content at the same
interval/version is a duplicate; object-key order is ignored, but distinct JSON value types remain
distinct. Non-finite or non-JSON raw content cannot enter reconciliation. Different content at that same identity raises `ValueError`,
even if a newer revision is also present. Values are never averaged and QA rank does not override
source version. Returned elements are detached copies; input is unchanged.

Run reconciliation **before** batch normalization and coverage reporting. A newer unusable,
invalid-QA or unavailable-value revision remains the selected raw element; normalization excludes
it, leaving the hour without an accepted observation. It must not revive an older accepted value.
All revisions require known quality metadata, valid nonnegative versions and completed aligned
UTC hourly intervals, including rejected or superseded revisions. Malformed data raises errors.
Known well-formed QA rejections still return no accepted observation.

This is a stateless bounded replay, not a persistent merge or a version cursor. To reconcile
multiple captures, supply their combined raw revisions (or retained current raw revisions plus
incoming rows), including rejected revisions. Passing only accepted observations loses withdrawals.
Missing rows in a later capture do not imply deletion. Empty input still validates the pilot IDs
and ingestion context. These rules are covered synthetically; the single historical capture does
not establish that real corrections occurred or demonstrate live incremental synchronization.

## Missing accepted hours

`missing_rnmca_hourly_intervals(observations, *, window_start, window_end)` receives normalized
accepted observations for the single pilot channel and explicit timezone-aware datetime boundaries.
It returns chronologically ordered `observedFrom`/`observedTo` UTC interval dictionaries for hours
without an accepted observation. The window starts at `window_start` and ends at `window_end`;
its duration must be positive and its boundaries must align to physical UTC hours. Each supplied
observation must span exactly one aligned UTC hour wholly inside the window; invalid boundaries
or out-of-window observations raise errors. Equivalent offsets are converted to UTC. Empty input
leaves all requested hours missing. Input order and repeated intervals do not change coverage.

This is accepted-observation coverage, not a statement that the provider omitted those rows.
Quality rejections also leave uncovered intervals. No values are filled, no versions are reconciled,
and duplicate coverage does not establish which version is authoritative. The caller selects one
channel and the requested window; deriving boundaries from accepted rows can hide edge gaps.

## October 24 historical replay

The archived seven-day capture spans `2026-10-04T21:00:00+00:00` through
`2026-10-12T08:00:00+00:00`: 179 physical UTC intervals, all present as raw rows.
Reconciliation followed by normalization yields 172 accepted observations and seven uncovered
accepted hours, all caused by known QA exclusions. Doubling and reversing the capture still yields
179 current raw revisions; replay is idempotent. This capture contains no duplicate interval or
observed correction history. Correction, conflict and withdrawal behavior is tested synthetically,
including a newer rejection followed by replay of the old accepted capture.

## Verification

Run `pytest tests/test_observation_time.py tests/test_import_rnmca.py tests/test_rnmca_observation.py tests/test_rnmca_window.py tests/test_rnmca_reconciliation.py`.
The tests cover the captured value and provenance, all captured status flags, QA acceptance and
rejection, zero/null overrides, malformed and unknown metadata, completed-hour boundaries,
temporal extrema, UTC ingestion, independent raw snapshots and synthetic DST intervals.
Synthetic calendar cases establish adapter behavior, not new provider observations at those dates.

The full archived seven-day DJ-1 / NO2 capture was replayed locally on October 22, 2026:
179 elements, 172 accepted and 7 excluded, with no unexpected errors. This is a historical
offline replay, not a new live collection or evidence of sustained availability.

Closure review also covered existing snapshot/release tests. CI uses pinned `uv==0.13.4`,
which removes the compromised emSign root certificate described in
<https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62>.

The full closure gate found a high-severity npm audit result for Vite 8.3.2. The reviewed
Dependabot #34 patch updates Vite to 8.3.4 and its affected transitive lock entries.
The fix includes <https://github.com/advisories/GHSA-rq7h-c2jc-7f22> and
<https://github.com/advisories/GHSA-vfpm-58rq-9qcg>; npm audit is rerun on the resulting lock.

Final local closure gate: 339 Python tests passed; 15 PostgreSQL/PostGIS tests skipped
without a local DSN and delegated to required CI. All 34 frontend tests, Ruff, ESLint,
snapshot validation and production build passed. npm audit reported zero vulnerabilities;
OSV returned no advisories for the 11 locked PyPI packages plus uv 0.13.4.
An independent check covered all 1,000 millisecond offsets at each of three dates
(2026, 2500 and 9999), 3,000 cases in total.

The closure also repairs the existing map time formatter: every Bucharest timestamp includes
its UTC offset. Repeated autumn 03:00 labels now distinguish GMT+3 from GMT+2, and spring
interval labels distinguish 02:00 GMT+2 from 04:00 GMT+3. The formatter lives in stationView
and is used directly by App; regression tests exercise that shared function.

## October 24 closure review

Review added 42 regression cases beyond the initial batch/window/reconciliation tests. It repaired
QA short-circuiting that hid malformed effective values and Python equality treating distinct JSON
types as equal in duplicate provenance. Tests cover these failures before and after repair, JSON-safe
raw provenance and the complete reconciliation → normalization → bounded-coverage path.

Local full gate: 443 Python tests passed; 15 PostgreSQL/PostGIS tests require the disposable CI DSN.
All 34 frontend tests, Ruff, ESLint, snapshot validation and production build passed. The existing
required CI gate exercises the database migrations and all 15 database cases before merge.

The npm audit found GHSA-mrhx-c7xc-m3w5 in transitive development dependency zod 4.6.2.
The lock now selects zod 4.6.7, within existing dependency ranges; npm audit reports zero
vulnerabilities. See the [GitHub advisory](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5).
OSV querybatch reported no advisories for 11 locked PyPI dependencies plus CI uv 0.13.4.
These are dated checks, not a guarantee about future advisories or untested behavior.
