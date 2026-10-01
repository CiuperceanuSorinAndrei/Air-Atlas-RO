# Atlasul Aerului

A public map of attributable air-quality observations in Romania. The current release is a
partial NO2/PM10 EEA demo, with a private database foundation and an activated manual history pilot.
It does not yet provide national live coverage, pollution scores, an API or a production freshness SLA.

## Current behavior

- The Python importer discovers EEA Parquet series, groups station/pollutant sequences and tries
  newer sequences first. It selects the latest row with validity 1–4; verification 1 is validated,
  2–3 preliminary. Hourly and daily intervals remain distinct.
- Every normalized row binds its sampling point, pollutant and station to the official HTTPS
  series URL. Values/coordinates must be finite, units are `ug.m-3`, timestamps use explicit offsets,
  and metadata for one physical station must agree. EEA's timezone-naive timestamps mean fixed
  UTC+1, not Romanian local time. Display uses Europe/Bucharest.
- Official ArcGIS metadata has an exact-ID Dataflow D fallback for no features or transient errors.
  Conflicting records fail visibly. Validated metadata expires after 24 hours; optional cache I/O
  failures do not discard a valid observation. Cache writes are atomic.
- Downloads allow only specific official HTTPS hosts and same-host redirects. Compressed Parquet
  is capped at 32 MiB, 200,000 rows, 128 MiB declared/decoded size and a bounded primitive schema.
- Publication rejects empty batches, inconsistent counts, duplicate station/pollutant pairs,
  missing previously published pairs and older observation intervals. Same-interval value
  corrections are allowed. Atomic, strict JSON writes preserve the old snapshot on failure.
- The map groups physical stations, clusters nearby markers and shows only reported intervals
  that have started, last at most one day and end less than six hours ago. In-progress intervals
  are labelled. This window describes freshness, not scientific quality or a health index.
- The frontend validates bounded JSON at runtime, polls the public snapshot once a minute and
  retains the last valid document after errors. Readings continue to expire. Keyboard-accessible
  markers and a text list provide access when map tiles fail. Source/licence links are visible.
- `collect_history` combines selected normalized series with duplicate/conflict and series-count
  checks. `scripts/sync_eea_history.py` is a manual, private PostgreSQL writer: a matching ETag
  skips history download; a changed full series replaces its accepted observations and ETag in
  one transaction. Source corrections and invalidations are retained; failures roll back.
  The writer reports new/missing series without automatically deleting or licensing them.
  Scheduled collection still selects latest observations; live history ingestion is manual.
- Six private Supabase/PostGIS tables are reproduced in `supabase/migrations`. RLS is enabled,
  no public policies are installed and application roles have no schema/table/sequence grants.
  Cross-source foreign keys, finite values, positive intervals and public-rights gates are tested.
  The `atlas_ingestor` migration starts without a login and grants only scoped EEA ingestion
  rights; a separate private login is activated for the pilot. The frontend and latest-map importer do not connect to this database.

## Development and checks

Use Node.js 24+ and Python 3.13 with [uv](https://docs.astral.sh/uv/).

```bash
npm ci --ignore-scripts
npm run dev
uv sync --locked
uv run --locked python scripts/import_eea.py
```

Run the exact code/data/build gate used by CI:

```bash
bash scripts/check.sh
```

It checks locked dependencies, Ruff lint/format, Python tests, the snapshot contract, ESLint,
frontend tests, npm advisories and the production build. Database CI additionally builds a fresh
PostGIS database from every migration and executes rollback-only fixtures; see
[scripts/check_database.sh](scripts/check_database.sh). Its disposable-database commands must
never be run against the live project.

The Vite base is `/Air-Atlas-RO/`. Production emits `dist/observations.json` separately from JS;
`npm run preview` serves the actual production output. See [operations](docs/operations.md) for
publication, failures and rollback, [roadmap](docs/roadmap.md) for remaining milestones and
[hardening review](docs/production-review-2026-09-30.md) for evidence and limits.

## Automation and hosting

CI runs on pushes and pull requests. Pages and refresh run the same gates, including SQL fixtures.
Actions and the PostGIS image are pinned to immutable revisions; Dependabot proposes updates.
Collection/build jobs have read access. Separate publication/deployment jobs receive the minimum
write permissions needed. Refresh stages only the snapshot and refuses a racing non-fast-forward push.

GitHub cron requests a run at minute 17 each hour, but actual execution can be delayed. Successful
refreshes trigger Pages through `workflow_run` because `GITHUB_TOKEN` pushes do not trigger normal
push workflows. Pages checks one checkout and deploys that checked artifact. The site and tile
provider have no availability guarantee. Failure-email delivery still requires independent evidence.

## Data attribution and licence

Observations: [European Environment Agency Air Quality Download Service](https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d).
Station metadata: [EEA Dataflow D](https://sdi.eea.europa.eu/catalogue/datahub/api/records/83eb503b-d132-4bf4-8f63-df56b7a80370/formatters/xsl-view?approved=true&language=eng&output=pdf).
EEA data is attributed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); Atlasul Aerului
selects, normalizes and filters it. No EEA endorsement is implied. Map data and tiles:
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright), subject to their
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
The [MIT licence](LICENSE) covers project code, not third-party data or trademarks.

## Verified history pilot — October 1

PR #10 passed PostgreSQL 17/Linux CI and was integrated as `1b6bdf8`. The private Supabase writer
uses a dedicated role through the session pooler with `sslmode=verify-full`. The licensed
EEA series `RO/SPO-RO0008R_00008_100` stored 2,080 accepted observations; a second sync returned
304 and preserved row IDs and ingestion timestamps. A populated private backup was restored
locally with matching values, geography and timestamps. This activates one manual history series;
scheduled national history and metadata-only refresh remain future work.
