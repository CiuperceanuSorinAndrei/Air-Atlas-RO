# National EEA catalog/inventory candidates — 2 November 2026

Local review candidates only. No registry approval, scheduled cutover, commit, PR, merge,
production refresh, deployment, infrastructure or database change belongs to this checkpoint.
The existing public importer continues using dynamic metadata.

## Source evidence

The exact input bundle is `data/eea/candidates/2026-11-02/`. `capture.json` records original
response hashes, lengths, URLs and retrieval times. The original XML is losslessly compressed as
`dataflow-d.xml.gz`; its source hash applies to **decompressed original bytes**. The other response
files are unchanged bytes. The captured Dataflow D directory, envelope and overview are retained
for independent inspection. The initial one-off capture used the same public requests; later
captures can use `scripts.capture_eea_candidates` as documented in [station-catalog](station-catalog.md).

Official delivery:
<https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml>

The captured envelope identifies Romania, reporting period 2025, reported `17 Aug 2026 08:52`
(no timezone shown), and status complete/technically accepted. Its displayed XML file date is
`09 Jun 2026`. These are distinct provider labels, preserved without inventing a delivery time
zone or conflating them with the November capture time. Header `D_ReportingHeader` covers
`2025-01-01T00:00:00+02:00` through `2026-01-01T00:00:00+02:00`. The source is an annual official
metadata delivery, not evidence that every listed station is currently operational.

## Candidate review

| Item | Result |
| --- | ---: |
| Official XML catalog stations | 210 |
| Dataset=1 discovered stations | 180 |
| Candidate station/pollutant channels | 717 |
| Candidate native streams | 717 |
| Independent ArcGIS station records | 196 |
| XML stations without discovered streams | 30 |
| XML stations without ArcGIS cross-check | 14 |
| Discovery stations absent from XML | 0 |
| Geometry discrepancies above 10 m among matched IDs | 0 |
| Name differences among matched IDs | 0 |
| XML reported ended operational periods | 16 |

All 717 discovered streams resolve to exact XML station identities. The 30 XML-only stations
remain in the catalog; their absence from discovery is not retirement. Missing cross-check IDs
are listed in `review-report.json` and do not establish agreement. Five of those 14 stations have
discovered streams: inspect the actual set before any later activation. Reported ended activity
periods are preserved as findings; they are not used to silently remove stations or channels.
None of the 16 stations with reported ended periods appears in this captured discovery response.

This is an initial proposal with **no approved national baseline**. Reconciliation against an
inventory built from the same discovery necessarily has no missing streams; that is internal
consistency, not independent completeness. No observation payloads were fetched, so stream
existence does not prove eligible readings, freshness, QA, continuity or public coverage.

## Offline reproduction

From the repository root, choose unused temporary paths:

```sh
gzip -dc data/eea/candidates/2026-11-02/dataflow-d.xml.gz > /tmp/atlas-national-source.xml
uv run --locked python -m scripts.build_eea_candidates \
  --xml /tmp/atlas-national-source.xml \
  --source-url https://cdr.eionet.europa.eu/ro/eu/aqd/d/envag8edw/dataflow-d-2025updateV2.xml \
  --reporting-year 2025 \
  --discovery data/eea/candidates/2026-11-02/discovery.txt \
  --arcgis data/eea/candidates/2026-11-02/arcgis.geojson \
  --output /tmp/atlas-national-replay
```

`tests/test_build_eea_candidates.py` independently checks the real capture hashes and reconstructs
all three candidate artifacts byte for byte. Other cases cover authoritative XML rejection,
unknown/duplicate discovery identities, geometry conflicts, previous-inventory losses, pure
offline construction, legacy parser compatibility, request bounds, and output preservation.

## Next gate

Inspect these candidates and their reported exceptions before approving any national registries.
Workflow integration and controlled degradation/corrective publication are separate subsequent
changes under [the accepted contract](reliability-and-history-contract.md). This checkpoint does
not unlock recurring history collection, national source expansion or an architecture restart.

## Dependency gate repair

The final audit found the existing ESLint toolchain's `minimatch@10.2.6` affected by
[GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249). A narrow package override
pins `minimatch` to the patched `11.0.1`; the lockfile changes that leaf and adds its
`@isaacs/nonrecursive-glob-match@1.0.3` dependency, without upgrading ESLint, TypeScript or React.
The published Node engine and CommonJS/ESM exports remain compatible with this project's Node24+
toolchain. Clean installation, actual lint, frontend tests, audit and build verify the consumers;
no production frontend dependency is added. Remove the override once upstream ranges admit a
patched version and a clean lockfile audit verifies that replacement.
