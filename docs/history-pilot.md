# Visible bounded history pilot — DJ-1 / NO2

Status, 21 November 2026: implemented locally. One captured historical hour
travels worker → private PostgreSQL/PostGIS → read API → interface. This is a
local pilot: nothing is deployed, the public site is unchanged, no recurring
collection exists and the storage model of
[bounded persistence](dj1-no2-history.md) is not redesigned.

## What is served

`scripts/history_api.py` is a small read-only HTTP server (standard library
plus the existing `psycopg` dependency). Under the site base path it serves:

- `api/health` — 200 only when the database accepts the reader role;
- `api/history/dj1-no2?from=…&to=…` — whole UTC hours with explicit offsets,
  `to` after `from`, at most 168 hours, no other parameters;
- every other path as a static file from the built site, so interface and API
  share one origin. The page CSP (`connect-src 'self'`) is unchanged and no
  CORS header exists.

The response lists accepted observations (interval in UTC, value as the exact
stored decimal text, source, report and ingestion time, validation fields,
normalization rule), the displayable sources with attribution and licence
link, and `missingIntervals` for every other hour of the window. `gapCause` is
always `unknown`: the API reads only accepted observations, so a missing hour
may be uncollected, rejected, ambiguous, in conflict or absent at the source.
It never reads the raw reports. Two stored readings for one hour are an
internal error, not a silent choice. Errors are generic: 400, 404, 405, 503,
500.

GET and HEAD only; ten-second socket timeout; one short read-only transaction
per request with statement and lock timeouts; path traversal and symlinks out
of the site directory return 404. It binds to 127.0.0.1 by default and is
**not hardened for public exposure**; a public deployment needs its own
reviewed front end, hosting and rights decision.

## Database access

Migration `20261121120000_dj1_no2_history_reader.sql` adds the NOLOGIN role
`atlas_pilot_reader`. It can select only named columns of `sources`,
`source_streams` and `observations`; RLS limits it to the two pilot streams
with verified rights, storage permission **and** `public_display_allowed`, and
to pilot NO2 hourly observations. It cannot read reports, devices, sites,
locations or record identifiers, and cannot write. Without the display flag
the API returns no observation and no source. The server issues
`SET ROLE atlas_pilot_reader` and refuses to answer if that role is superuser
or bypasses RLS.

`python -m scripts.sync_dj1_no2_history` is now also a command: it replays
captured RNMCA/EEA JSON for an explicit window using
`ATLAS_PILOT_INGESTION_DSN` and `SET ROLE atlas_pilot_ingestor`. Repeating it
stores nothing new.

## Interface

The panel "Istoric pilot · DJ-1 · NO₂" is compiled only when the build has
`VITE_PILOT_HISTORY=1`. The public Pages build does not set it and contains
neither the panel nor a request to the API. The panel shows one row per UTC
hour with Romanian local time, value, source, validation state and ingestion
time; missing hours are explicit rows with an unknown cause; attribution is
shown per source. The response is validated before display.

## Compose

```sh
cp /dev/null .env   # then set POSTGRES_PASSWORD, ATLAS_WORKER_PASSWORD, ATLAS_API_PASSWORD
docker compose up --build --wait db web
docker compose --profile replay run --rm worker
open http://127.0.0.1:8080/Air-Atlas-RO/
```

`db` has no published port and initializes once from the repository
migrations plus `ops/pilot/seed.sql`. That seed marks the two pilot streams as
displayable **for the disposable local database only**; it is configuration
for the demonstration, not evidence of a display licence, and must not be
copied to a deployed database. `web` and `worker` run as an unprivileged user
on a read-only filesystem. `.env` is ignored by Git.

`scripts/check_pilot_stack.sh` starts the stack, checks the empty window, runs
the worker twice, restarts the database and each time compares the API with
`tests/fixtures/pilot_history_response.json` and fetches the page and panel
script from the same origin. CI runs it as the `pilot-stack` job.

## Evidence and limits

- PostgreSQL tests (`tests/test_history_api.py`, `supabase/test_foundation.sql`)
  cover role denials, display gating, the captured hour (RNMCA
  `48.81861538888885 ug.m-3`, 2026-10-10 18:00–19:00 UTC; 19:00–20:00 missing),
  input limits, traversal and failure responses. The same fixture file is the
  API expectation and the interface contract test.
- 21 November: the full chain was run as local processes against a disposable
  PostgreSQL 18 / PostGIS cluster and viewed in a real browser at desktop
  width and 390 px.
- **Docker was not available locally. The Dockerfile, `compose.yaml` and the
  CI job have not been executed**; the check script was exercised only with
  Docker commands replaced by the same local processes. Their first real run
  is the pull-request CI.
- One captured hour is the whole data set. No deployment, backup, retention or
  recurring ingestion is implied.
