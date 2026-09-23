# Atlasul Aerului

Atlasul Aerului is a public web project for exploring air-quality observations across Romania on
an interactive map.

## Current checkpoint

- React, TypeScript, Vite and Leaflet frontend
- one marker per physical monitoring station
- observation interval, preliminary status and source provenance shown per reading
- manually run Python importer that discovers Romanian NO2 and PM10 EEA Parquet series
- series grouped by physical station and pollutant, with higher sequence indices attempted first
  and older sequences used as fallbacks when needed
- each attempted series keeps its latest observation with EEA validity code `1`, `2`, `3` or `4`
  and skips series without a valid observation
- official station metadata fetched on demand and persisted in the ignored local
  `.cache/eea_station_metadata.json` cache; when ArcGIS returns zero features or has a temporary
  HTTP 5xx/network failure, an EEA Dataflow D fallback validates the Romanian station name and
  coordinates before caching
- verification code `1` maps to `validated`; codes `2` and `3` map to `preliminary`
- each reading is labelled `Măsurare recentă` or `Măsurare veche` at render time from its
  `observedTo` timestamp, using an explicit 24-hour display threshold
- recoverable per-series download, Parquet, metadata and normalization errors do not stop the
  remaining selected series from being attempted
- `src/data/observations.json` contains the normalized observations plus an import summary with
  attempted, imported, skipped and failed series counts
- the frontend displays the import summary and consumes the observations through the unchanged
  generic station-grouping and map path
- 38 Python tests, Ruff, production build and ESLint checks passing
- owner visual review on 2026-09-21 confirmed that the expanded map, markers and popups render and
  remain usable

The 2026-09-23 manual run discovered 503 series across 355 station/pollutant groups. It attempted
357 candidates and wrote 349 observations from 195 stations: 15 more station/pollutant pairs than
the previous snapshot, with no previously published pair lost. All 12 station IDs previously absent
from ArcGIS were imported through the Dataflow D fallback. Seven candidates had no valid observation;
one failed because Dataflow D returned two names/coordinate pairs for `RO0150A` across 29 rows.
That station was not in the previous snapshot and is still excluded pending investigation. At review
time, 309 observations were less than 24 hours old and 40 were older.

This result improves useful Romanian coverage but must not be described as complete or live national
coverage. The 24-hour label is a transparent display rule, not a scientific quality classification.
A completed batch exposes per-series skips and failures; a discovery or process failure before the
JSON is written cannot update this static report.

The Dataflow D fallback is used after an ArcGIS response with zero features, HTTP 5xx or a network
timeout/failure. It requires an exact Romanian station ID, nonempty name, valid coordinates, a
complete result page and one unique station name/coordinate combination. Malformed data and
non-transient HTTP errors remain visible. The AQViewer filter endpoint is an internal web-application
interface and may change. A full live importer run succeeded on 2026-09-23 despite the ArcGIS outage.

Before replacing the JSON, the importer rejects an empty batch, duplicate station/pollutant pairs,
a count mismatch or a batch missing any previously published pair. The accepted JSON is written to
a temporary file and atomically replaces the old snapshot. A real station's retirement therefore
requires a reviewed change to the publication baseline. These guards prevent an accidental coverage
regression; scheduled refreshes and failure alerts are still future work.

## Run locally

Requirements: a current Node.js release and npm.

```bash
npm install
npm run dev
```

Vite prints the local URL in the terminal.

To run the manual EEA importer, install Python 3.13 and
[uv](https://docs.astral.sh/uv/), then run:

```bash
uv sync
uv run python scripts/import_eea.py
```

## Checks

```bash
npm run build
npm run lint
uv run pytest
uv run ruff format --check scripts/import_eea.py tests
uv run ruff check scripts/import_eea.py tests
```

## Next milestone

Investigate the conflicting Dataflow D records for `RO0150A` without guessing coordinates. Then
expand the EEA importer from NO2 and PM10 to SO2, O3 and PM2.5 and review coverage, freshness and
failed-series counts.

Later milestones include additional Romanian data providers, provider-aware deduplication,
pollution scoring, a backend, persistence, scheduled refreshes and public hosting. Each source must
pass access, licence, attribution, quality and overlap checks before publication.

## Data attribution

The current example observations originate from the
[European Environment Agency Air Quality Download Service](https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d).
EEA is identified as the source in the application. Fallback station metadata comes from the
[EEA Dataflow D catalogue](https://sdi.eea.europa.eu/catalogue/datahub/api/records/83eb503b-d132-4bf4-8f63-df56b7a80370/formatters/xsl-view?approved=true&language=eng&output=pdf),
which identifies CC BY 4.0 terms. Source data remains subject to its own terms; the repository's
MIT licence applies only to the project code.

Map tiles and map data are provided by
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright) and are attributed in the
map interface.

## Licence

Project code is available under the [MIT License](LICENSE). Third-party data, map tiles, libraries
and trademarks retain their respective terms.
