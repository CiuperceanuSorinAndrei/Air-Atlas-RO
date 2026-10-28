# Accepted reliability and history delivery contract

Accepted 28 October 2026 after review of the implementation and proposed failure policies.
This document is a plan and acceptance boundary, not a claim that the catalog, degraded
publication or revised history storage exists. The XML repair is the current implementation.

## Delivery order

1. Close the XML metadata repair: complete review and PostgreSQL CI, protected release,
   national refresh, served-artifact/data verification and monitoring. Successful import and
   recent source measurements are separate evidence. Save the verified recovery checkpoint.
2. Verify Geoapify restrictions with separate development/production configuration where
   practical; reconcile documentation, source identity/deduplication and AQI method references.
   Provider settings are unconfirmed until read back; documentation is not configuration proof.
3. Introduce the approved station catalog and channel inventory, then controlled degradation
   under the policies below, as separate reviewed changes. Do not clone collectors per station.
4. Complete the bounded historical worker/database/read-API/frontend pilot, testing Compose
   early for the accepted single-VPS target. Do not require a complete storage redesign before
   the first visible historical series; new recurring collection remains disabled.
5. Before recurring ingestion or broader history rollout, separate evidence/state/execution
   storage, resolve the duplicated metadata lookup including HTTP 304, measure retention and
   backup needs, and verify replay/recovery. Preserve source-specific permissions and the
   existing external-backup/restore prerequisites. Fit cluster bounds and use semantic
   aggregation labels as independent UI improvements, without delaying reliability work.

CAMS surface models, temporal exploration and European AQI remain subsequent product work.
Sentinel-5P columns remain separate from ground concentrations. AQI combines the same hour
under the official pollutant/completeness rules; hourly points do not establish daily-limit
exceedances. No universal 25 km station-representativeness assumption is accepted.

## Approved metadata and inventory

Maintain an approved, versioned station catalog separately from the approved channel inventory
and from the generated latest-observation snapshot. A channel is a station/pollutant pair;
sampling sequences and native stream identities remain explicit within that channel.
Snapshot omissions never silently shrink the inventory or clear unresolved degradation.

The station catalog records exact provider IDs, geometry, authoritative XML delivery provenance
(envelope/file URL, official delivery metadata, reporting year and XML SHA-256), and independently
verified cross-provider mappings. Do not identify a new delivery by reporting year alone or map
RNMCA/EEA IDs solely by name or distance. For EEA, the unique official `AQD_Station` is primary;
ArcGIS is a cross-check. An unresolved authoritative contradiction requires review.

The measurement job reads the approved catalog without live ArcGIS/AQViewer/CDR lookups for
known stations. Check the catalog weekly and on request. Proposed new stations, renames,
relocations and corrections require reviewed PRs. The approved catalog has no automatic
seven-day runtime expiry. A geodesic discrepancy above 10 m triggers review, not automatic
source selection; differences at or below 10 m are not proof of identity or correctness.
Unregistered discovery channels are reported separately for inventory review and are not
blindly published or counted as approved channels. Bound and validate discovery responses.

## Controlled degradation and publication

The initial operational budget is at most six distinct approved channels and three distinct
stations simultaneously degraded and unrecovered. Exceeding either limit blocks normal
publication. These are adopted initial policy limits, not statistically demonstrated safety.
Count each channel once even if it has multiple failure causes. An omitted channel still
consumes the budget until recovered or explicitly removed from the approved inventory.

An isolated absence from discovery is unavailability, not retirement, regardless of observation
age. An isolated transport, format or metadata failure may degrade a channel under the budget.
Discovery failure and invalid global output/contracts block normal publication; detect partial
discovery against the approved inventory. Unconfirmed interval regressions remain forbidden.
Only a successfully downloaded/decoded sequence with no eligible observations permits trying
a lower-priority sequence. Hard transport/schema/identity failures do not silently fall back.

Keep three policies distinct:

- Measurement freshness: the existing six-hour definition, respecting the different explicit
  UI and served-monitor treatment of in-progress versus completed intervals.
- Dynamic metadata cache: 24-hour normal TTL; previously validated metadata with provenance
  may be reused during transient network/5xx failure up to seven days old. This transitional
  cache policy does not impose expiry on the approved catalog. An authoritative identity or
  geometry contradiction is not a transient error and cannot be silently bypassed.
- Observation retention: a source-reconfirmed latest valid historical reading can remain with
  its real age. Transport fallback lasts at most 24 hours from the last demonstrable source
  confirmation, without changing observation/ingestion timestamps. At expiry omit the reading
  but retain its degraded status against the inventory.

`lastSourceConfirmedAt` must bind a reconciled observation/state to its verified source version
or ETag, not merely an HTTP 200 or successful decoding. Do not manufacture it from measurement,
ingestion or migration time. Legacy records without evidence get no invented fallback window.

An unresolved authoritative station-identity/geometry conflict quarantines that station on the
map: no new readings or marker at contested coordinates; preserve historical evidence and count
all affected approved channels against the degradation budget.

If the normal batch is blocked but a prior observation is confirmed withdrawn, allow a separately
validated strictly corrective publication. It may exclude only confirmed withdrawn readings and
stations quarantined for authoritative identity/geometry conflict; all remaining previous
observations stay unchanged. No new positive observations may enter through this path. Preserve
incident/degradation reporting. Measurement withdrawal and geometry quarantine remain distinct
reasons, not interchangeable QA conclusions. Never delete historical evidence as a side effect.

## History evidence, state and execution

Separate source evidence/revisions, current reconciled channel state and a lightweight execution
log. Evidence keys use native provider identity and original labels, not mandatory canonical UTC
for ambiguous/nonexistent local hours. Preserve native versions, QA, units, reporting times,
rejections/withdrawals and the fields needed for deterministic replay. Define provider-specific
canonical payloads excluding only processing metadata; hashing only value/QA is insufficient.

Local `revision_seq` records insertion order only. RNMCA native versions and the verified EEA
interval/reporting-time contract determine current state. Older captures cannot revive a withdrawn
newer version. Different payloads for the same native version, or EEA interval/report time, remain
conflicts even across separate runs. Do not generalize DJ-1 temporal semantics nationally without
source evidence; specific fixtures and proven channel rules remain legitimate.

Semantic state identity includes the window and processing context, including normalization,
QA and mapping versions. Replaying the same evidence/window/context at a later execution time
creates no new source revision or changed semantic state and causes no unnecessary observation
rewrite. Execution/audit identity is separate from semantic deduplication. Do not silently change
the meaning of the existing full-report audit hash. Read relevant current state rather than all
prior overlapping execution reports; neither naive LIMIT 1 nor accepted observations alone can
reconstruct withdrawals and partial-window evidence.

Changed normalization/context forces controlled reprocessing even if source bytes are unchanged;
retain original evidence and provenance. Measure real row/JSONB/index/dump sizes and external
backup/restore needs before adopting retention or enabling recurring ingestion. Unlimited evidence
retention is not implicitly promised by an hourly row-count estimate. Exact SQL layout and measured
retention settings are implementation-stage gates, not reasons to delay the bounded visible pilot.

## Required acceptance cases

- Successive small losses accumulate against the approved inventory; recovered channels cease
  consuming budget. Six channels/three stations pass; seven channels or four stations block.
- Isolated discovery absence degrades without implying retirement; unregistered channels remain
  separate; malformed or broadly incomplete discovery cannot replace the accepted state.
- Empty eligible sequences permit fallback; a hard error in the preferred sequence does not.
- Confirmation is not refreshed by invalidating or identity-mismatched content; legacy migration
  cannot invent it. At 24-hour fallback expiry the omitted channel remains degraded.
- Transient metadata failure can use eligible 36-hour cache data, never eight-day cache data;
  authoritative conflict cannot use that fallback. Known catalog stations make no live metadata
  calls; catalog changes generate reviewed diffs; >10 m discrepancy produces a review finding.
- Normal budget exceeded plus confirmed withdrawal/quarantine permits only corrective exclusions,
  with no newly collected positive readings or silent incident reset.
- Exact replay and changed replay time have stable semantic state; changed source revision changes
  only affected state; stale replay cannot revive withdrawals. Same-native-version conflicts remain
  conflicts across runs; new normalization replays evidence without losing ambiguity or rejections.

## Explicitly deferred without implementation commitment

Changing the `production-checks` mechanism, moving snapshots out of main, replacing the OSM tile
provider, retiring Supabase, an alternative rolling 72-hour JSON history and US NowCast each
require a separate decision. No remote migration, infrastructure purchase or provider-setting
change is authorized merely by this plan. The existing VPS/Compose budget and recovery decisions
remain applicable. This plan records agreement; it does not implement future components.
