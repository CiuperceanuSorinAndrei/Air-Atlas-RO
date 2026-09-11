# Air Atlas RO

Air Atlas RO (`Atlasul Aerului`) is a public web project for exploring air-quality observations
across Romania on an interactive map.

## Current checkpoint

- React, TypeScript, Vite and Leaflet frontend
- one marker per physical monitoring station
- separate NO2 and PM10 readings in the Bucuresti B-1 popup
- observation interval, preliminary status and source provenance shown per reading
- production build and ESLint checks passing

The current observations are small, real EEA fixtures used to prove the data model and map flow.
They are not yet fetched automatically, so this version must not be presented as live coverage or
as a complete national pollution index.

## Run locally

Requirements: a current Node.js release and npm.

```bash
npm install
npm run dev
```

Vite prints the local URL in the terminal.

## Checks

```bash
npm run build
npm run lint
```

## Next milestone

Replace the hand-written fixtures with a manually run EEA importer that discovers reported
stations and measurements, normalizes them into the existing observation contract and produces a
collection consumed by the unchanged grouping and map code.

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
