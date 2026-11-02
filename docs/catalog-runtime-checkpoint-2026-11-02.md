# Registry-backed national collection — local checkpoint, 2 November 2026

The owner delegated technical review, implementation, tests and documentation for this checkpoint.
`data/eea/registries/v1/` is technically accepted for explicit **local collection trials**. This is
not a claim that the owner personally inspected each station, national provider completeness,
production registry approval or scheduled cutover. The tooling can be released independently
from registry activation. The existing no-argument refresh command and workflow are unchanged.

## Reviewed baseline and exception dispositions

`catalog.json` and `inventory.json` preserve the first captured proposal: 210 official XML stations,
180 stations with dataset=1 discovery streams, and 717 exact station/pollutant channels/streams.
The separate `review.json` records the delegated technical review and its scope; `manifest.json`
pins the **exact file-byte SHA-256** of all three documents and both registry versions.
The manifest provides change detection for reviewed local inputs, not a cryptographic signature
or an authorization mechanism against an operator who can replace the manifest and every file.

The review rechecks original XML bytes, header/year/period/hash, and every catalog station's exact
identity/name/geometry. All 717 inventory sampling points occur uniquely in the same reporting
header and XML with matching `gml:id`, INSPIRE local ID/namespace, parent station and observed
pollutant. None has a reported activity end. This is stronger evidence than extracting station IDs
from filenames alone. No mapping is inferred from a name or nearby coordinates.

| Finding | Technical disposition |
| --- | --- |
| 14 XML stations lack ArcGIS records, including 5 with streams | Retain authoritative XML geometry/identity; record absent secondary evidence explicitly. No geometry agreement is claimed for these stations. |
| 30 catalog stations have no inventory channel in the captured discovery | Retain catalog records; invent no channels; do not infer retirement from discovery absence. |
| 16 catalog stations have reported ended activity periods | Retain historical identities; none is assigned a reviewed inventory channel. |
| No previous national approved baseline | Accept this explicit initial local-trial selection; do not claim the provider response establishes national completeness. |

A declared ended sampling point/station with an inventory channel, missing/duplicate sampling
identity, mismatched parent/pollutant or contradiction in original station metadata fails technical
review. Cross-check geometry discrepancies above 10 m remain a blocker; absent ArcGIS is distinct.

To reproduce the technically reviewed bundle into a new directory:

```sh
gzip -dc data/eea/candidates/2026-11-02/dataflow-d.xml.gz > /tmp/atlas-registry-source.xml
uv run --locked python -m scripts.eea_registry \
  --xml /tmp/atlas-registry-source.xml \
  --catalog data/eea/candidates/2026-11-02/catalog-candidate.json \
  --inventory data/eea/candidates/2026-11-02/inventory-candidate.json \
  --output /tmp/atlas-reviewed-registry
```

This command requires an authorized technical review; generating a file does not grant production
approval. XML provenance is checked offline. Runtime collection reads the pinned bundle without
XML, ArcGIS, AQViewer or metadata-cache lookups for known stations. No catalog TTL is introduced.

## Opt-in importer path

```sh
uv run --locked python -m scripts.import_eea \
  --registry-manifest data/eea/registries/v1/manifest.json \
  --discovery data/eea/candidates/2026-11-02/discovery.txt \
  --previous-snapshot src/data/observations.json \
  --output-directory /tmp/atlas-registry-trial
```

`--output-directory` is mandatory with `--registry-manifest`; `--check` cannot be combined with a
trial. Trial-only flags without a manifest fail. The default no-argument command retains its old
behavior. Omitting `--discovery` fetches the existing bounded six-pollutant Romanian discovery;
providing it fixes the exact captured response and its SHA-256 for a reproducible membership check.
Parquet measurement collection is a **network operation**, even with captured discovery.

The loader checks bounded files, duplicate keys, fixed filenames, exact raw hashes, schema and
versions, technical-review scope/counts/provenance, and absence of unresolved geometry findings
before observation I/O. Missing/corrupted/unreviewed inputs cannot fall back to dynamic metadata.
Every approved stream must appear in discovery, including all registered fallback sequences;
this strict rule is unchanged. Unknown streams are reported and excluded. Missing approved
streams/channels, transport errors, malformed schemas and identity failures block the output.

Successfully decoded streams without eligible rows retain the existing `NoValidObservationsError`
behavior: no invented observation, no freshness bump, and an explicit `channelsWithoutObservation`
entry against the fixed inventory. A decoded-empty preferred sequence alone permits trying the
next registered sequence. Hard errors do not. An entirely empty collection still fails.
This report is **not** persistent degradation tracking, six-channel/three-station budget approval,
source-confirmation/fallback logic, withdrawal/quarantine handling or corrective publication.

Existing snapshot publication guards run against a private copy of `--previous-snapshot`. The
actual input file is never overwritten; recent pair losses and unconfirmed interval regressions
block output. Regression verification may make additional Parquet requests under the existing
source-invalidation rules. The previous input's report hash binds its original bytes. If no
previous snapshot is supplied, no previous-state loss/regression comparison can be claimed.

Only after all validation succeeds, the completed snapshot and `collection-report.json` are
atomically exposed together under a new output directory. Existing directories/symlinks are
refused. Errors leave existing snapshots and registry documents unchanged. Exit 0 means a valid
local result under these checks; it proves neither fresh measurements nor production deployment.

## Verification and next gate

Automated cases cover XML sampling identities/periods, pinned-input tampering, unsupported scopes,
version/provenance inconsistencies, default CLI compatibility, explicit report gaps, excluded
unknown streams, hard failures, recent-loss/regression guards and filesystem preservation.
The real reviewed bundle reconstructs byte for byte from the captured official XML and proposal.

A national trial reads all 717 registered Parquet streams with dynamic metadata/cache access
explicitly forbidden. Per-response URL/ETag/byte count/SHA receipts, accepted observations, gap
report, original-source samples and executed logs are retained in the checkpoint evidence.
Executed result: 717 unique registered streams / 130,419,067 downloaded bytes, 640 eligible
observations, 77 decoded-empty channels, zero failed imports and zero dynamic metadata/cache calls.
A new discovery response independently matched all 717 streams with no missing/unregistered URL;
its byte order differed from the first capture, without changing membership. The selected 640
station/pollutant pairs all match the previous snapshot's pair set, with no interval regression.
Five retained Parquet samples independently verify the source selection/value/unit/time handling.

At `2026-11-02T15:48:28.308040+00:00`, only **4/640** selected observations were recent completed
measurements within six hours; latest `observedTo` was `2026-11-02T15:00:00+01:00`.
Successful ingestion is distinct from provider measurement freshness. Stale readings keep their
original times and are not relabelled fresh. No production health/SLA claim follows from this trial.

The initial network trial ran while non-selection loader/report integrity refinements were being
finished. Its full validated observation document was then revalidated offline through the final
loader, snapshot guard and report emission using fresh discovery and the unchanged previous
snapshot. The final snapshot is byte-identical to the network-collected result; this was not a
second source selection or an invented source confirmation. Both original trial and final emission
are retained, along with receipts and executed verification scripts.

Production registry approval and scheduled integration remain separate from releasing the
reviewed tooling and accepting local technical trials. Controlled degradation and
corrective publication remain the next implementation checkpoint under
[the accepted contract](reliability-and-history-contract.md); no history rollout is enabled.

The closure regression also covers direct `python scripts/import_eea.py --registry-manifest ...`
invocation without a configured `PYTHONPATH`; it follows the same guarded mode as the module CLI.
