# Atlasul Aerului frontend design

Decision: October 8, 2026. Status: latest-known frontend implemented and checked locally; owner accepted the current design on October 8. Further visual changes are deferred; publication remains pending. Hourly model products remain planned.

## Ownership and visual direction

The assistant designs and implements this frontend redesign; the owner reviews and steers.
Use a light map-first interface with restrained Liquid Glass-inspired web materials, teal accents,
charcoal text, rounded controls and restrained motion. This is a web interpretation, not native
Apple Liquid Glass. Avoid redundant headings and explanatory overlays on the map.
Keep Romanian labels, legible diacritics, visible keyboard focus and reduced-motion support.
The project identity, source attribution and evidence boundaries remain visible.

## Layout and primary journey

Desktop: compact header; approximately 336 px sidebar; map fills remaining space; time controls
below the map. Sidebar contains locality/station search, explicit geolocation action, pollutant
filters, results and selected-station details. Mobile: search above the map, results/details below
it in an expandable panel with explicit buttons; dragging is not required.

Locality selection centres the map and lists nearby stations with distances. Search accepts
names with/without diacritics and distinguishes same-name localities by county. Use a locally
served GeoNames Romanian populated-place catalogue, retain stable IDs and source attribution.
This is a place finder, not evidence of complete administrative or measurement coverage.
Geolocation is requested only after user action; denial must leave normal search usable.

Latest-known readings remain visible even when expired. Each pollutant retains its interval,
unit, source, preliminary/validated status and age. A station-level recent badge must not imply
all its pollutants are recent. Teal/grey freshness encoding is distinct from pollution severity.
Separate last source measurement, last accepted import and browser fetch status. The browser
cannot infer the last attempted ingestion run from an old public snapshot.

## Hourly air-quality exploration

Owner clarification, October 8: the final product is continuous hourly air-quality exploration,
not merely navigation between available station observations. Freshness does not determine whether
an hour can be selected. For every hour within the supported product range, aim to publish a spatial
estimate even where station observations are missing. This is a product target, not an existing
coverage or availability guarantee.

The primary future view combines a modelled concentration surface with independently inspectable
measured station points. A measurement describes its station and source interval; it does not replace
an entire model grid cell or prove locality-wide measured coverage. Any observation-adjusted surface
is a separate derived product with a versioned, evaluated method. Missing station hours may have
modelled estimates; stale station readings must never be relabelled as measurements for that hour.

Use a bottom hourly timeline with date/time selection, previous/next hour, a return-to-current-hour
action and optional user-initiated playback. The hour step is fixed, not the next available observation.
Display Europe/Bucharest local time while identifying hours by UTC instants; show offsets where
needed to distinguish repeated daylight-saving hours. The supported range derives from published
model/history artifacts. Gaps remain selectable and explain what is unavailable; playback never
silently jumps over them. Map, details and legend share the selected pollutant, hour and product.

Example: at 14:00 a station may have no measurement while the model supplies a valid estimate for
14:00. Display the surface and label its value "Modelat"; the station detail says "Fără măsurătoare
pentru această oră". If a measured value exists for its corresponding source interval, show it as
"Măsurat" alongside the model estimate, preserving the original averaging period. An hourly control
does not convert a daily observation into an hourly measurement. A last-known observation, if
requested, remains separately labelled with its original interval and never supplies the selected-hour
measured value.

Show provenance near each value: measured/modelled, valid hour or observation interval, source,
model run/version, spatial resolution and uncertainty or validation information. Model age is separate
from valid time. Do not invent numeric confidence scores. If no usable model or measurement exists,
show "Estimare indisponibilă" with a reason and retry where applicable; do not fabricate a value to
satisfy the continuity target. Forecasts, analyses and reanalyses retain distinct product labels and
revision history. Late observations may support a new derived revision, never an unlabelled overwrite.

A practical first modelling candidate is the CAMS European ensemble: official hourly analyses and
forecasts at approximately 10 km resolution, with hourly reanalyses for historical coverage. This
is a candidate requiring access/licence, pollutant inventory, validation and publication checks, not
a selected or integrated provider. Hourly time resolution does not imply street-level spatial accuracy.

Today the public contract is latest-only. Keep the useful latest-known measurements view with visible
age as an interim view, explicitly distinct from the future current-hour product. Render an honest
history-unavailable state until a bounded public hourly dataset or endpoint exists. The private
single-stream history pilot is not public multi-station history or a national hourly product.

## Layer and colour semantics

- Measured: station points with source, per-pollutant concentration and observation interval.
  No inferred coverage circles or continuous coloured field from isolated measurements.
- Modelled: independently labelled grids/surfaces with model/run, valid time, resolution, units
  and uncertainty where available. Do not silently substitute modelled values for observations.
- Satellite: separate column-density context with acquisition time, units, QA and missing/cloud
  coverage. Never relabel satellite columns as surface concentrations.

Only enable layers whose licensed public artifacts are implemented and verified. The current
release supports measured EEA points. Model/satellite controls explain unavailability and do not
pretend to load data. A future interpolated ground surface is another derived product requiring
validated spatial assumptions and explicit uncertainty.

Colouring numerical values uses a documented per-pollutant scale with visible units and legend;
freshness is a separate visual dimension. EU AQI categories await the official method, completeness
and QA gate. Grey/no-data must never mean clean air. Layer blending preserves provenance and
missing-data semantics; measurements and model estimates remain inspectable separately.

## Delivery sequence and checks

1. Responsive shell, typography, map, results and station-detail presentation.
2. Latest-known/freshness behaviour, polling recovery, source details and honest time/layer states.
3. GeoNames catalogue/search, disambiguation, nearby-station distance and geolocation fallback.
4. Frontend tests, lint/build, desktop/mobile Playwright checks, keyboard paths and loading/error/
   stale/partial/recovery states. Full release checks before any publication.
5. Deliver a bounded public hourly concentration product: source/model inventory and rights, public
   historical station intervals, model ingestion/history artifacts, validation and visible provenance.
   Then activate hourly controls and model surfaces; observation fusion/downscaling is a separately
   evaluated improvement. Satellite context remains a separate layer. Private API/database credentials never enter
   browser assets; a dedicated domain-restricted geocoder browser key is explicitly public.

Record local implementation, visual acceptance, CI, publication and served-artifact verification
as distinct outcomes. This document does not delegate backend/database changes or publication.

## Local implementation and verification — October 8

Implemented: responsive light map/sidebar, station details and pollutant filters; visible age for
latest-known observations; separate latest measurement/import timestamps; 15,421 GeoNames places
with county/alias search; explicit geolocation action and failure message; model/history unavailable
states; source/licence disclosure; keyboard skip link, station/detail focus and mobile panel toggle.
No public history/model artifact, AQI calculation, database migration or deployment is part of this
local frontend result.

| Before | After | Why |
| --- | --- | --- |
| Generic page styling around the map | Full-width light atlas with a sidebar and map-first mobile order | Put location and geographic exploration first |
| Old readings disappear from the displayed map | Latest-known readings stay visible with explicit age and recent counts | Preserve useful source context without implying current measurements |
| No independent locality catalogue | Locally served GeoNames search with county disambiguation | Support locality discovery without a geocoder autocomplete dependency |
| Station list loses focus when opening details | Focus moves to the selected station heading and returns to the results heading | Preserve keyboard orientation and reveal details on mobile |

Verification: full `scripts/check.sh` passed: 149 Python tests passed, 15 database-dependent tests
skipped without a database DSN; 27 frontend tests passed; lint, snapshot validation, dependency audit
(zero advisories) and production build passed. Subsequent UI-only focus/message refinements passed
lint/build. Playwright verified actual Craiova selection, nearby distance ordering, station/pollutant
details, mobile collapse/expand, keyboard skip/return focus and absence of horizontal overflow at
320, 390, 768, 1024 and 1440 px. Browser-only failure simulations verified locality retry and accepted
snapshot retention/recovery across polling failures. A temporary mixed-recency fixture verified
per-pollutant recency rather than implying all readings at a station are fresh. Browser fixtures
never modify the published snapshot. Desktop/mobile screenshots were inspected locally.

Owner acceptance, remote CI, commit/publication and served-production verification remain distinct
and have not been completed by these local checks.

## Visual simplification after owner review — October 8

The owner found the first iteration too heavy and requested a cleaner Apple Liquid Glass-inspired
appearance. Remove the header country/context line, map country/slogan card, map layer card and
map freshness legend. Keep navigation controls and required tile attribution on the map. Put the
source, legend, recency counts and model/history availability inside native "Detalii despre date"
below the map; retain a visible stale badge and per-reading age even with details closed.

Use system typography, lightly tinted translucent panels, broad rounded corners, pill-shaped
controls, fewer separators and a compact measurement summary. Decorative blur stays on interface
surfaces, not the text or measurement values. Use opaque fallbacks when backdrop filtering is
unsupported, transparency is reduced or contrast is increased. Existing reduced-motion handling
remains active. No new frontend libraries are required.

| Before | After | Why |
| --- | --- | --- |
| Country/context, map title and slogan | Brand, search and geographic content | Remove repeated information |
| Layer and freshness cards cover the map | Source and legend in expandable details below it | Keep map exploration unobstructed |
| Strong panel divisions and rectangular filters | Soft translucent surfaces and a pill-shaped segmented filter | Reduce visual weight while preserving hierarchy |

Local verification for this iteration: 27 frontend tests, lint and production build passed.
Playwright checked that redundant overlays are absent, freshness metadata is outside the map,
and responsive widths from 320 to 1440 px have no horizontal overflow. Desktop/mobile captures
were inspected. Owner visual acceptance and remote publication remain pending.

## Pollutant context, index design and Romania focus — October 8

Owner review: restore plain-language pollutant names without a map overlay; place air-quality
categories in the all-pollutants view; rewrite and relocate source information; remove the generic
brand symbol; show Romania without neighbouring-country content.

Implemented locally: selected pollutant names below the segmented filter; typographic project
wordmark; native "Despre date și surse" disclosure at the base of the sidebar (after results on
mobile), with source, unit, interval, recency and licence details. "Toți" has a European-index area
explicitly labelled as pending calculation, not a fabricated score. Source information distinguishes
implementation unavailability from missing observations. Freshness still does not mean air quality.

Use the European Air Quality Index as the planned principal indicator, with translated categories,
valid hour, contributing/worst pollutant and measured/modelled provenance. Do not invent a 0–100
score, average pollutant values or average station indices into a locality score. CO stays visible as
a concentration but is outside the five-pollutant European index. Before activating categories, verify
and version official concentration bands/rounding, station-type completeness rules, hourly alignment,
quality flags and behaviour for negative, missing, daily, stale and revised observations. A latest-only
snapshot is not proof that all station/pollutant rows share an hour. US AQI would be a separately
labelled method. This iteration does not implement index calculation.

The country-mask experiment was rejected in the next owner review because panning looked unnatural.
The current implementation uses the normal continuous OSM basemap, initially fitted to Romania,
with the country reset action. The mask, outline, country-only pan/zoom limits, bundled boundary
geometry and unused boundary attribution were removed. Station clustering remains unchanged.

| Before | After | Why |
| --- | --- | --- |
| Generic three-line brand mark | Project name as a wordmark | Remove an unsupported identity symbol |
| Pollutant abbreviations without context | Full pollutant name beneath the selector | Explain the current selection where the user made it |
| Header information button and vague copy | Source disclosure beside the data workflow, with concrete intervals and units | Keep the explanation discoverable and useful |
| Country-mask experiment | Restored continuous basemap, initially centred on Romania | Preserve natural map movement after owner review |

Local checks for this iteration: 27 frontend tests, lint/build and diff whitespace checks passed.
Browser checks covered selected pollutant names, the explicit pending-index state, source disclosure,
responsive widths 320–1440 px, locality/station selection on mobile, country reset and cluster spacing.
The mask experiment was inspected and then rejected by the owner; the normal basemap was restored. Index calculation, owner visual
acceptance and publication are not completed by these checks.

## Exact addresses and location provenance — October 8

The owner requests a full address search (street, house number, locality) and air quality at the
chosen point, labelled by measurement/model origin. This extends the existing GeoNames locality
finder; GeoNames populated-place centroids are not exact-address geocoding.

Journey: enter an address; select a disambiguated result; confirm its map pin and match precision;
choose pollutant/hour; inspect air-quality provenance at that point. Distinguish building/address,
street-only and locality-only matches. An unmatched house number must not silently become an
"exact" city centroid. Offer input correction and pin adjustment rather than a false exact match.
Geolocation retains its reported accuracy; address precision does not imply model resolution.

Use an address-geocoding provider with documented Romanian address support and permitted search
behaviour. The owner selected Geoapify. The explicit-submit forward-geocoding adapter supports Romanian
address results, country filtering and match precision. The owner key is configured locally and a
live public-address request returned HTTP 200; mobile selection retained the approximate-match label. See [address search setup](address-search.md). Keep the local GeoNames search usable without it. Design bounded requests, stale-response
cancellation, empty/error/retry states, matching confidence and appropriate attribution. Do not
send precise address queries to an external service while the user is only searching local stations.
Public Nominatim is not the default: its usage policy prohibits autocomplete, requires a deliberate
informed developer choice, has a one-request-per-second application-wide cap, attribution/caching
and switchability requirements. The integration remains inactive when no provider browser key is configured. Domain restrictions
and production configuration are not independently verified. Browser fixtures are test evidence only.

At each selected point/pollutant/hour:

1. Use a station-supported value only when a validated representativeness area contains that point
   and a compatible measurement exists for the selected interval. Label "Măsurat la stația X",
   show distance, source interval and evidence for representativeness. Do not imply an instrument
   physically measured at the entered address.
2. Otherwise show the valid model estimate for the containing grid cell, with "Modelat", valid hour,
   model/run, grid resolution and available uncertainty. Unknown station coverage remains unknown;
   it must not be described as proven outside coverage. Show nearby station readings as references.
3. If neither exists, say "Estimare indisponibilă pentru această locație și oră" and retain nearby
   stations with their actual age. Never use a fixed-radius circle or nearest-station distance as
   scientific coverage, and never fabricate a modelled fallback when no model artifact exists.

Coverage is pollutant-, metric-, time- and method-specific; obtain authoritative polygons or an
evaluated representativeness method before activating station-to-point assignment. A daily reading
cannot fill an hourly slot. Re-evaluate source selection for every hour in the history view. If an
index combines measured and modelled inputs, expose each input provenance and mark the derived
result as mixed rather than wholly measured. No national/locality average is inferred from proximity.

Current evidence: latest-only station points and locality search; no public model grid or validated
station representativeness polygons; the exact-address adapter awaits owner credentials and a live check. These are separate data and
integration dependencies, not implemented coverage promises.

## References

- [Project roadmap](roadmap.md)
- [Operations and freshness policy](operations.md)
- [GeoNames dump contract and licence](https://download.geonames.org/export/dump/readme.txt)
- [EEA index reference](https://airindex.eea.europa.eu/AQI/)

- [CAMS European hourly analyses and forecasts](https://ads.atmosphere.copernicus.eu/datasets/cams-europe-air-quality-forecasts?tab=overview)
- [CAMS European hourly reanalyses](https://ads.atmosphere.copernicus.eu/datasets/cams-europe-air-quality-reanalyses?tab=overview)

- [Apple HIG: Materials](https://developer.apple.com/design/human-interface-guidelines/materials)

- [European AQI methodology and completeness](https://airindex.eea.europa.eu/AQI/?webgl=0)

- [Geoapify address autocomplete and match precision](https://apidocs.geoapify.com/docs/geocoding/address-autocomplete/)
- [Nominatim public-service usage policy](https://operations.osmfoundation.org/policies/nominatim/)
- [EEA monitoring-station classification](https://www.eea.europa.eu/en/topics/in-depth/air-pollution/monitoring-station-classifications-and-criteria/)
