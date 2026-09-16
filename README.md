# Atlasul Aerului

Atlasul Aerului is a public web project for exploring air-quality observations across Romania on
an interactive map.

## Current checkpoint

- React, TypeScript, Vite and Leaflet frontend
- one marker per physical monitoring station
- observation interval, preliminary status and source provenance shown per reading
- manually run Python importer that discovers EEA Parquet series and official station metadata
- deterministic five-series import that keeps each series' latest observation with EEA validity
  code `1`, `2`, `3` or `4` and skips series without a valid observation
- verification code `1` maps to `validated`; codes `2` and `3` map to `preliminary`
- each reading is labelled `Măsurare recentă` or `Măsurare veche` at render time from its
  `observedTo` timestamp, using an explicit 24-hour display threshold
- recoverable per-series download, Parquet, metadata and normalization errors do not stop the
  remaining selected series from being attempted
- `src/data/observations.json` contains the normalized observations plus an import summary with
  attempted, imported, skipped and failed series counts
- the frontend displays the import summary and consumes the observations through the unchanged
  generic station-grouping and map path
- nine focused Python tests, Ruff, production build and ESLint checks passing

The checked-in JSON is a small, manually refreshed EEA sample used to prove the end-to-end data
path. Selecting the first five discovered series is a bounded technical checkpoint, not national
coverage or a source-specific freshness guarantee. The 24-hour label is a transparent display rule,
not a scientific quality classification or proof that the manually refreshed sample is live. A
completed batch exposes per-series skips and failures; a discovery or process failure before the
JSON is written cannot update this static report.

## Run locally

Requirements: a current Node.js release and npm.

```bash
npm install
npm run dev
```

Vite prints the local URL in the terminal.

To run the bounded EEA importer, install Python 3.13 and
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

Replace the deterministic first-five-series sample with a justified selection policy that increases
useful Romanian station and pollutant coverage without implying national completeness.

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
