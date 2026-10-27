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
identity must remain consistent. Conflicting metadata is rejected after reading
the complete result. Neither partial pages nor guessed/averaged coordinates are
accepted or cached.

The publication CLI stops on its first hard import failure, because any such
failure already forbids replacing the public snapshot. This avoids repeatedly
querying a broken metadata path until the 30-minute workflow limit. Empty valid
series still allow the existing sequence fallback. Programmatic collection keeps
its diagnostic default and can explicitly request `fail_fast=True`. A failed CLI
import preserves the previous snapshot and exits with an actionable error.

These changes fix pagination and wasted work. They do not claim to repair an
unavailable external service or contradictory official records. Public freshness
remains an operational incident until a validated full import and served-data
check succeed. Metadata TTL, source identity, publication completeness and monitor
thresholds are unchanged.

The dependency integration includes PRs #29, #30, #31, #33, #35, #37, #38, #39,
#40 and #41. React and ReactDOM are aligned to 19.3.1; the original React-only
update failed at runtime despite its utility tests passing. A real ReactDOM
initialization test covers this compatibility boundary. PyArrow 26 and Ruff
0.17.1 are tested with the current source and database suites; the older Ruff
PR's failed audit predates the already-published Zod remediation.

## Supabase usage

Live read-only inspection found project `Air-Atlas-RO` active and healthy,
containing 2,080 observations and one stream from the original history pilot.
Latest ingestion/synchronization was 1 October 2026; the private schema still
has the original six tables and no `pilot_reports`. No database Cron table was
present. The public frontend reads the Pages snapshot and the scheduled workflow
imports EEA into JSON; neither uses Supabase. The new bounded persistence writer
was exercised in disposable local/CI PostgreSQL/PostGIS, not applied to the live
Supabase project. Active hosting status does not mean the app uses the database.
