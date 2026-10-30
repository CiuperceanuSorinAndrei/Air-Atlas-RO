# EEA station catalog and channel inventory

Local implementation, 30 October 2026. This is the first catalog/inventory slice, not a national
rollout. No approved national registries are shipped, no scheduled refresh is switched to them,
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
