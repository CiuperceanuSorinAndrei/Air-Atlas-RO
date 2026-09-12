# Atlasul Aerului

Atlasul Aerului is a public web project for exploring air-quality observations across Romania on
an interactive map.

## Current checkpoint

- React, TypeScript, Vite and Leaflet frontend
- one marker per physical monitoring station
- separate NO2 and PM10 readings in the Bucuresti B-1 popup
- observation interval, preliminary status and source provenance shown per reading
- manually run Python importer that discovers EEA Parquet series and station metadata, selects the
  latest row from one series and emits one normalized observation
- production build and ESLint checks passing

The frontend still uses small, real EEA fixtures to prove the data model and map flow. The importer
currently processes only the first URL returned by EEA and prints one observation to standard
output; it does not yet generate the frontend collection. This version must not be presented as
live coverage or as a complete national pollution index.

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
uv run ruff format --check scripts/import_eea.py
uv run ruff check scripts/import_eea.py
```

## Next milestone

Extend the bounded importer from one discovered series to a normalized collection, write it to a
local JSON file and replace the hand-written fixtures without changing the generic station grouping
and map code.

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
