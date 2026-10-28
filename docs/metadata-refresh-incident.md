# Metadata refresh incident and dependency integration

Verified on 27 October 2026: the official ArcGIS station metadata endpoint
returned HTTP 502 in direct probes. The existing Dataflow D fallback was
reachable, but exact station IDs could have different names/coordinates in the
same reporting year, import timestamp and source XML. Choosing the newest year
or import timestamp therefore does not resolve the ambiguity.

Captured RO0078A and RO0141A responses contain two coordinate variants. RO0143A
returns 34 records in two pages (30 plus 4), also with conflicting metadata.
The regression fixture projects relevant fields from the original captured
responses and records their byte counts/SHA-256. It is not a new provider record.

The fallback now reads all advertised pages, bounded to 64 pages/2,000 records
per station. Page identity, page count, total row count, progress and exact station
identity must remain consistent. After reading the complete result, conflicting
table metadata requires the official reporting-document resolution described below.
Neither partial pages nor guessed/averaged coordinates are accepted or cached.

The publication CLI stops on its first hard import failure, because any such
failure already forbids replacing the public snapshot. This avoids repeatedly
querying a broken metadata path until the 30-minute workflow limit. Empty valid
series still allow the existing sequence fallback. Programmatic collection keeps
its diagnostic default and can explicitly request `fail_fast=True`. A failed CLI
import preserves the previous snapshot and exits with an actionable error.

The October 27 changes fix pagination and wasted work. They do not claim to repair an
unavailable external service or contradictory official records. Public freshness
remains an operational incident until a validated full import and served-data
check succeed. Metadata TTL, source identity, publication completeness and monitor
thresholds are unchanged.

The dependency integration includes PRs #29, #30, #31, #33, #35, #37, #38, #39,
#40 and #41. React and ReactDOM are aligned to 19.3.1; the original React-only
update failed at runtime despite its utility tests passing. A real ReactDOM
initialization test covers this compatibility boundary. PyArrow 26 and Ruff
0.17.1 are tested with the current source and database suites; the older Ruff
PR's failed audit concerned older Vite versions; current Vite 8.3.4 already
contains that remediation.

## Official reporting-document resolution — October 28

Live RO0008R metadata returned 36 rows on two pages, with two positions about 17 m
apart. Both variants occur in reporting year 2025 with the same source XML and
import timestamp. The original XML contains one `AQD_Station` for `STA-RO0008R`,
named `EM-3`, at longitude 25.134839 / latitude 47.324686. The corresponding SO2
sampling point explicitly refers to that station. The same document contains
unique station features for RO0078A, RO0141A, RO0143A and RO0150A. All five metadata
lookups succeeded through the new path during live verification.

On a complete but conflicting table response, reporting year selects the current
available reporting document, not a winning coordinate row. Every row must have a
valid reporting year; candidates from that reporting year must use `RO.ANPM.AQ` and identify
exactly one source document. Multiple documents at that year remain a hard error;
the importer does not fall back to an older document if the selected one fails.

Only the official CDR host and Romanian Dataflow D envelope/XML paths are allowed.
Legacy HTTP links on that exact host are upgraded to HTTPS before fetching;
credentials, other hosts, nonstandard ports, query strings and fragments are
rejected. Existing same-host redirect, byte/deadline and HTTP completeness checks
apply. XML is limited to 32 MiB, decoded as UTF-8, and rejects DTD/entity declarations.

The document must have one namespaced reporting header for `RO.ANPM.AQ`, an explicit
offset-bearing January-to-January reporting period matching the selected year, and
a content reference to the station. The station must be unique and its EoI code,
local ID, GML ID and namespace must agree. Required scalar fields and geometry must
be unique. Only the observed EPSG:4326 representation with two latitude/longitude
ordinates is accepted; values must be finite and within geographic bounds.

The metadata cache retains the XML HTTPS URL, reporting year and SHA-256. Up to four
validated documents are reused within the process so stations sharing one report
do not repeatedly download it. The existing 24-hour station-cache TTL remains.
No measurement value, unit, quality flag, timestamp normalization or snapshot
schema is changed. Unresolved conflicts preserve the previous cache and snapshot.

`tests/fixtures/eea_reported_stations.json` records the original XML byte count and
SHA-256 and projects the complete captured RO0008R pages. Its sibling XML projects
the original header identity/period/content references and five original station
features; it is deliberately not represented as the complete provider document.
Regression coverage includes the real conflict, all five station features, exact
axis order, document reuse, all six pollutant/unit paths, invalid identities,
competing reports, unsafe URLs/XML and cache/CLI snapshot preservation.

Successful local metadata lookups do not establish public freshness. A complete
validated import, approved deployment and served-data monitoring remain separate
evidence before declaring the incident resolved publicly.

Local verification: 651 Python tests passed, with 63 PostgreSQL integration cases
skipped because no disposable database was configured; all 35 frontend tests and
Ruff checks passed. The new XML suite adds 62 cases. An isolated national import
used the application's existing collector on six disjoint station partitions,
with separate station-cache files, then combined and validated the complete result
through `write_observation_snapshot` against the previous snapshot. It attempted
all 717 discovered files: 640 imported observations across 180 stations, 77
valid-empty skips and zero failed imports. The resulting JSON SHA-256 was
`b43a80d454315c6b24a8a6b3e8e09ed4c99455614f3778c8591ad1df6c9fd43f`.
This diagnostic concurrency was temporary; the production CLI remains sequential.
No public data, deployment, Git commit or remote database was changed.

## Supabase usage

Live read-only inspection found project `Air-Atlas-RO` active and healthy,
containing 2,080 observations and one stream from the original history pilot.
Latest ingestion/synchronization was 1 October 2026; the private schema still
has the original six tables and no `pilot_reports`. No database Cron table was
present. The public frontend reads the Pages snapshot and the scheduled workflow
imports EEA into JSON; neither uses Supabase. The new bounded persistence writer
was exercised in disposable local/CI PostgreSQL/PostGIS, not applied to the live
Supabase project. Active hosting status does not mean the app uses the database.
