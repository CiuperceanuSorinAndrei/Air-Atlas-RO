# EEA station catalog and channel inventory

Candidate and local-trial tooling, 2 November 2026, extending the 30 October validators. This is not a national
rollout. The shipped national artifacts are candidates; no approved registries or scheduled cutover exist,
and controlled degradation remains planned. Synthetic test records are not approval evidence.

## Separate reviewed inputs

A catalog has `schemaVersion: 1`, a positive integer `catalogVersion`, and 1–2,000 `stations`.
Each EEA station requires exactly:

- `stationId`, `stationName`, finite WGS84 `latitude` and `longitude`.
- `namespace: RO.ANPM.AQ` and `localId: STA-<stationId>` from the unique official XML station.
- `metadataSourceUrl`: exact HTTPS Romanian Dataflow D XML file URL on `cdr.eionet.europa.eu`.
- `metadataEnvelopeUrl`: the file's envelope directory URL, including the trailing slash.
- `metadataReportingYear`: integer year from 2013 through the current UTC year.
- `metadataSourceSha256`: lowercase SHA-256 of the original XML bytes.
- `metadataDelivery`: exact `headerId`, `reportingPeriodStart` and `reportingPeriodEnd` from the
  official reporting header; timezone-aware annual boundaries matching the reporting year with consistent UTC offsets.

An optional `arcgis` object contains `stationId`, `stationName`, `latitude`, `longitude` for the
independent cross-check. A spherical geodesic discrepancy above 10 m is a review finding and
blocks catalog-backed collection, without choosing or rewriting either geometry. A smaller
difference does not establish identity. Catalogs have no automatic seven-day expiry; review
weekly/on demand. Schema validation checks consistency, not the truth of a delivery: approval
still requires examining official XML/provenance and reviewing the proposed artifact change.
Cross-provider mappings are outside schema v1; no RNMCA identity is inferred from name/distance.

An inventory has `schemaVersion: 1`, positive `inventoryVersion` and `catalogVersion` matching
the catalog, and 1–12,000 `channels`. Each channel has `stationId`, canonical pollutant name
(`SO2`, `PM10`, `O3`, `NO2`, `CO`, `PM2.5`) and 1–32 `streams`. Each stream records exact
`samplingPointId` and `sourceUrl`. These must agree on station, pollutant and numeric sequence.
A channel is one station/pollutant pair; duplicate channels or numeric sequences are rejected.
Array order does not assign priority: the existing collector tries descending numeric sequence.
Unknown fields are rejected instead of silently accepting unimplemented policies.

## Offline inspection

From the repository root:

```sh
uv run --locked python -m scripts.eea_catalog --catalog /path/to/catalog.json --inventory /path/to/inventory.json
uv run --locked python -m scripts.eea_catalog --catalog /path/to/catalog.json --inventory /path/to/inventory.json --discovery /path/to/discovery.txt
```

Discovery uses the provider's UTF-8 `ParquetFileUrl` header and one full URL per line. Reads are
bounded before JSON decoding: 8 MiB per registry, 2 MiB per discovery payload; discovery allows
at most 16,000 unique valid URLs. Duplicate JSON keys, excessive JSON nesting and duplicate discovery URLs are rejected.
Output is JSON on stdout; there are no network requests, snapshot writes or cache writes.
Exit 0 means consistent inputs with no review finding; exit 1 means invalid/unreadable inputs;
exit 2 means valid inputs requiring review (missing approved streams/channels, unregistered
streams, or an ArcGIS discrepancy). A valid report with empty matches does not authorize publishing.

`reconcile_discovery` reports matched streams, missing channels, missing streams, unregistered
channels/streams and cross-check findings. A newly discovered sequence of a known channel does
not replace its approved sequence. Omissions never mutate the inventory or imply retirement.

## Bounded collection integration

Python callers may use `scripts.eea_catalog.collect_catalog_observations(urls, catalog, inventory)`.
It validates both registries, requires every approved stream to be present, and refuses unresolved
cross-check findings before observation network I/O. Unregistered streams remain in its discovery
report and are excluded from collection. Known stations use catalog metadata without ArcGIS,
AQViewer, CDR or metadata-cache access. The returned object has `collection` (the existing import
result) and `discovery`; it does not write or publish a snapshot. Persist the report alongside any
future candidate; it is not a replacement for publication validation or persistent incident state.

The low-level `collect_observations(..., station_catalog=catalog)` validates catalog station
membership, but does not enforce an inventory. Use the wrapper above for inventory-backed work.
The default importer command and scheduled national refresh continue using the existing dynamic
metadata path. Observation JSON schema, source labels and timestamp semantics are unchanged.

Only a successfully decoded sequence with no eligible observations permits trying an older
sequence. A hard transport/schema/identity error stops that channel even when `fail_fast=False`;
`fail_fast=True` blocks the batch immediately. Failure counters count actual attempted sequences.
Duplicate numeric series identities are rejected before fetching. This repairs the old non-fast
collector's silent hard-error fallback; unrelated channels can
still be inspected in non-fast mode.

## Remaining rollout gates

Build and independently review real national artifacts from official XML deliveries and exact
stream discovery; do not promote the generated latest snapshot into an approved inventory.
Then integrate catalog/inventory loading into the measurement job through a separate reviewed
change. Persistent degradation, six-channel/three-station budgeting, source confirmation windows,
withdrawal/quarantine publication and history changes remain governed by
[the accepted contract](reliability-and-history-contract.md), not this inspection tool.

## Reproducible national candidate construction

The offline builder and the separate network capture command are available locally. Neither
approves registries, reads the latest-observation snapshot, collects measurements, changes a
workflow, or publishes anything. Schema v1 remains unchanged.

First inspect the official Romanian Dataflow D directory/envelope and choose the exact delivery
URL and reporting year. A reporting year alone cannot identify a delivery. Capture public inputs
into a **new directory**:

```sh
uv run --locked python -m scripts.capture_eea_candidates \
  --source-url https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml \
  --reporting-year 2025 --output /tmp/atlas-eea-source-capture
```

The capture command uses existing HTTPS host/redirect, timeout and response-byte protections:
32 MiB XML, 2 MiB discovery, 8 MiB ArcGIS, 2 MiB envelope. It requests Romanian dataset=1 streams
for all six supported pollutants, and a bounded ArcGIS RO station cross-check. It records original
response bytes and a manifest with exact URLs, HTTP method/request body, retrieval UTC, ETag,
byte length and SHA-256. Retrieval time is capture evidence, not measurement freshness or source
confirmation. Invalid XML/discovery/cross-check responses fail before exposing an output bundle;
ArcGIS transfer-limit responses are rejected. These separate requests are not a transactional
provider snapshot. Envelope HTML is retained for human delivery review, not automatically treated
as metadata approval. Capture does not automatically select a newer official delivery.

Generate candidates entirely offline from those captured files:

```sh
uv run --locked python -m scripts.build_eea_candidates \
  --xml /tmp/atlas-eea-source-capture/dataflow-d.xml \
  --source-url https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml \
  --reporting-year 2025 \
  --discovery /tmp/atlas-eea-source-capture/discovery.txt \
  --arcgis /tmp/atlas-eea-source-capture/arcgis.geojson \
  --output /tmp/atlas-eea-candidates
```

Outputs are `catalog-candidate.json`, `inventory-candidate.json` and `review-report.json`.
Exit 0 means a candidate bundle was constructed; it **never means approved** or complete national
coverage. Exit 1 means invalid/unreadable inputs or unavailable output. Output directories must
not exist, including empty directories or symlinks. Serialization and staging complete before
atomic directory replacement of an exclusively reserved empty destination; errors clean up staging.
An interrupted reservation can leave an empty destination, never a partly written registry set.

The catalog includes every uniquely identified XML station, even if no dataset=1 stream is
currently discovered. The inventory includes exactly the discovered supported channels whose
station identity resolves in that XML. Discovery-only stations and streams remain unresolved
in the report; no dynamic metadata lookup or guessed cross-provider mapping fills the gap.
Duplicate URLs or numeric stream identities fail. Inventory sorting is station/pollutant then
descending numeric sequence. The collector still owns execution priority.

Review every report field before any later registry approval:

- `approvalState: candidate-only`, `requiresOwnerApproval: true` always remain explicit.
- `discoveryCompleteness` says a single response cannot establish completeness. Without an
  approved prior inventory there is no loss baseline; zero missing candidate streams is not proof
  of complete national discovery, since this candidate inventory was constructed from discovery.
- `xmlOnlyStations`, `discoveryOnlyStations`, missing/extra cross-check IDs, name differences and
  geodesic discrepancies above 10 m are retained. Missing cross-checks are unknown, not matches.
  Ended operational periods reported in XML remain visible; catalog inclusion proves neither
  current operation nor measurement availability.
- Exact XML/discovery/ArcGIS SHA-256 values identify replay inputs. Official annual periods retain
  their original offsets. Reordering discovery preserves the registries but changes its byte hash.

For subsequent proposals, supply **both** `--previous-catalog` and `--previous-inventory` and
explicit larger `--catalog-version` / `--inventory-version`. `baselineDiff` records complete
before/after station/channel changes and discovery reconciliation against the previous inventory,
including missing channels and streams. Previous documents are never modified or silently
shrunk. Baseline hashes use the documented deterministic JSON encoding, not the original input
file bytes. New candidates remain subject to review, including proposed removals.

The first real captured bundle and its review limitations are documented in
[national candidate review](national-catalog-candidates-2026-11-02.md). It is stored under
`data/eea/candidates/2026-11-02/`, outside the public frontend bundle and refresh configuration.


## Technically reviewed local registry collection

The [next local checkpoint](catalog-runtime-checkpoint-2026-11-02.md) adds exact-byte registry
manifests, original-XML sampling-point review and explicit opt-in importer trials. The first
`data/eea/registries/v1/` bundle is accepted technically for local trials, with documented exception
dispositions. Production approval/cutover and controlled degradation remain separate future gates.
