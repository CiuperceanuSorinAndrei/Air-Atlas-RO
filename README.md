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
  `.cache/eea_station_metadata.json` cache
- verification code `1` maps to `validated`; codes `2` and `3` map to `preliminary`
- each reading is labelled `Măsurare recentă` or `Măsurare veche` at render time from its
  `observedTo` timestamp, using an explicit 24-hour display threshold
- recoverable per-series download, Parquet, metadata and normalization errors do not stop the
  remaining selected series from being attempted
- `src/data/observations.json` contains the normalized observations plus an import summary with
  attempted, imported, skipped and failed series counts
- the frontend displays the import summary and consumes the observations through the unchanged
  generic station-grouping and map path
- 19 focused Python tests, Ruff, production build and ESLint checks passing
- owner visual review on 2026-09-21 confirmed that the expanded map, markers and popups render and
  remain usable

The 2026-09-21 manual run discovered 503 series across 355 station/pollutant groups. It attempted
360 candidates and wrote 334 observations from 183 stations: 154 NO2 and 180 PM10, with no duplicate
station/pollutant pairs. The batch classified seven candidates as having no valid observation and
19 as failed. Eighteen failed attempts belong to 12 station IDs for which the official ArcGIS
metadata service returned zero features; one candidate timed out. At review time, 301 observations
were less than 24 hours old and 33 were older.

This expanded result improves useful Romanian coverage but must not be described as complete or
live national coverage. The 24-hour label is a transparent display rule, not a scientific quality
classification. A completed batch exposes per-series skips and failures; a discovery or process
failure before the JSON is written cannot update this static report.

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

Publish the reviewed generated checkpoint. Then investigate a justified metadata fallback for the
12 EEA station IDs absent from the current ArcGIS station layer without inventing locations or
silently dropping provenance.

Later milestones include additional Romanian data providers, provider-aware deduplication,
pollution scoring, a backend, persistence, scheduled refreshes and public hosting. Each source must
pass access, licence, attribution, quality and overlap checks before publication.

## Data attribution

The current example observations originate from the
[European Environment Agency Air Quality Download Service](https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d).
EEA is identified as the source in the application. Source data remains subject to its own terms;
the repository's MIT licence applies only to the project code.

Map tiles and map data are provided by
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright) and are attributed in the
map interface.

## Licence

Project code is available under the [MIT License](LICENSE). Third-party data, map tiles, libraries
and trademarks retain their respective terms.
