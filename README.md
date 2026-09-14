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
- normalized observations are written to `src/data/observations.json` and consumed by the generic
  station-grouping and map path
- focused Python tests, Ruff, production build and ESLint checks passing

The checked-in JSON is a small, manually refreshed EEA sample used to prove the end-to-end data
path. Selecting the first five discovered series is a bounded technical checkpoint, not national
coverage or a freshness guarantee. Individual readings can be stale, so this version must not be
presented as live coverage or as a complete national pollution index.

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

Add explicit freshness and import-error states before describing observations as current. Then
replace the deterministic five-series sample with a justified selection policy that increases
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
