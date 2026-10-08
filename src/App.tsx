import './App.css'
import { useEffect, useRef, useState } from 'react'
import { divIcon, type LatLngTuple, type Point } from 'leaflet'
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import {
  acceptSnapshot,
  groupByStation,
  latestImport,
  latestMeasurement,
  type ObservationDocument,
  type AirQualityObservation,
} from './airQualityObservation'
import { readSnapshot } from './fetchSnapshot'
import { searchAddresses, type AddressResult } from './addressSearch'
import { isRecentObservation } from './observationFreshness'
import {
  distanceKm,
  measurementAge,
  searchLocalities,
  searchText,
  validateLocalities,
  type Locality,
} from './stationView'

const pollutants = ['Toți', 'PM2.5', 'PM10', 'NO2', 'O3', 'SO2', 'CO']
const pollutantLabels: Record<string, string> = {
  Toți: 'Toți',
  'PM2.5': 'PM₂.₅',
  PM10: 'PM₁₀',
  NO2: 'NO₂',
  O3: 'O₃',
  SO2: 'SO₂',
  CO: 'CO',
}
const pollutantNames: Record<string, string> = {
  'PM2.5': 'Particule fine',
  PM10: 'Particule în suspensie',
  NO2: 'Dioxid de azot',
  O3: 'Ozon',
  SO2: 'Dioxid de sulf',
  CO: 'Monoxid de carbon',
}
const formatTime = (value: string | number) =>
  new Date(value).toLocaleString('ro-RO', {
    timeZone: 'Europe/Bucharest',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
const formatValue = (value: number) =>
  value.toLocaleString('ro-RO', { maximumFractionDigits: 2 })
const romaniaBounds: [LatLngTuple, LatLngTuple] = [
  [43.55, 20.2],
  [48.3, 29.8],
]
const addressKey = import.meta.env.VITE_GEOAPIFY_API_KEY?.trim() ?? ''
const initialNow = Date.now()
type MapFocus = {
  latitude: number
  longitude: number
  label: string
  precision?: string
}

function StationMarkers({
  stations,
  selected,
  now,
  onSelect,
}: {
  stations: AirQualityObservation[][]
  selected: string | null
  now: number
  onSelect: (id: string) => void
}) {
  const map = useMap()
  const [zoom, setZoom] = useState(() => map.getZoom())
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) })
  const groups: { point: Point; readings: AirQualityObservation[][] }[] = []
  for (const readings of stations) {
    const station = readings[0]
    const point = map.project([station.latitude, station.longitude], zoom)
    const group =
      station.stationId !== selected && zoom < 12
        ? groups.find(
            (group) =>
              group.readings[0][0].stationId !== selected &&
              group.point.distanceTo(point) < 42,
          )
        : undefined
    if (group) group.readings.push(readings)
    else groups.push({ point, readings: [readings] })
  }
  return groups.map(({ readings: group }) => {
    const hasRecent = group.some((readings) =>
      readings.some((reading) => isRecentObservation(reading, now)),
    )
    if (group.length > 1) {
      const { latitude, longitude } = group[0][0]
      return (
        <Marker
          key={group.map((readings) => readings[0].stationId).join(',')}
          position={[latitude, longitude]}
          title={`${group.length} stații. Mărește harta.`}
          icon={divIcon({
            className: `station-cluster ${hasRecent ? 'is-recent' : 'is-old'}`,
            html: `<span>${group.length}</span>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16],
          })}
          eventHandlers={{
            click: () =>
              map.setView([latitude, longitude], Math.min(zoom + 2, 12)),
          }}
        />
      )
    }
    const station = group[0][0]
    return (
      <Marker
        key={station.stationId}
        position={[station.latitude, station.longitude]}
        title={`${station.stationName} (${station.stationId}) · ${hasRecent ? 'Are date recente' : 'Date vechi'}`}
        icon={divIcon({
          className: `station-marker ${hasRecent ? 'is-recent' : 'is-old'} ${selected === station.stationId ? 'is-selected' : ''}`,
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        })}
        eventHandlers={{ click: () => onSelect(station.stationId) }}
      >
        <Popup>
          <strong>{station.stationName}</strong>
          <br />
          {station.stationId}
          <br />
          {hasRecent ? 'Are măsurători recente' : 'Ultimele valori cunoscute'}
          <br />
          <button
            className="popup-button"
            onClick={() => onSelect(station.stationId)}
          >
            Vezi măsurătorile
          </button>
        </Popup>
      </Marker>
    )
  })
}

function MapPosition({ focus }: { focus: MapFocus | null }) {
  const map = useMap()
  useEffect(() => {
    if (focus) map.setView([focus.latitude, focus.longitude], 11)
  }, [focus, map])
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map])
  return null
}

function App() {
  const [now, setNow] = useState(initialNow)
  const [document, setDocument] = useState<ObservationDocument | null>(null)
  const [dataError, setDataError] = useState(false)
  const [tilesFailed, setTilesFailed] = useState(false)
  const [pollutant, setPollutant] = useState('Toți')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [origin, setOrigin] = useState<MapFocus | null>(null)
  const [focus, setFocus] = useState<MapFocus | null>(null)
  const [localities, setLocalities] = useState<Locality[] | null>(null)
  const [placesError, setPlacesError] = useState(false)
  const [placesLoading, setPlacesLoading] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [locationMessage, setLocationMessage] = useState('')
  const [locating, setLocating] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [limit, setLimit] = useState(30)
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const addressRequest = useRef<AbortController | null>(null)
  const [addressResults, setAddressResults] = useState<AddressResult[] | null>(
    null,
  )
  const [addressLoading, setAddressLoading] = useState(false)
  const [addressError, setAddressError] = useState('')

  useEffect(() => () => addressRequest.current?.abort(), [])

  function cancelAddressSearch() {
    addressRequest.current?.abort()
    addressRequest.current = null
    setAddressLoading(false)
    setAddressResults(null)
    setAddressError('')
  }

  async function submitAddress() {
    if (!addressKey || query.trim().length < 3) return
    cancelAddressSearch()
    const controller = new AbortController()
    addressRequest.current = controller
    setAddressLoading(true)
    setSearchOpen(true)
    try {
      const addresses = await searchAddresses(
        query,
        addressKey,
        AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]),
      )
      if (addressRequest.current === controller) setAddressResults(addresses)
    } catch {
      if (addressRequest.current === controller)
        setAddressError(
          'Adresele nu s-au încărcat. Reîncearcă sau alege o localitate.',
        )
    } finally {
      if (addressRequest.current === controller) {
        addressRequest.current = null
        setAddressLoading(false)
      }
    }
  }

  function chooseAddress(address: AddressResult) {
    cancelAddressSearch()
    const point = {
      latitude: address.latitude,
      longitude: address.longitude,
      label: address.label,
      precision: address.precision,
    }
    setOrigin(point)
    setFocus(point)
    setQuery(point.label)
    setSearchOpen(false)
    setSelected(null)
    setLimit(30)
    setPanelOpen(true)
  }

  useEffect(() => {
    if (selected)
      detailHeading.current?.focus({
        preventScroll: !window.matchMedia('(max-width: 760px)').matches,
      })
  }, [selected])

  useEffect(() => {
    let stopped = false
    let pending = false
    let current: ObservationDocument | null = null
    let controller: AbortController | null = null
    async function refresh() {
      if (pending) return
      pending = true
      controller = new AbortController()
      const timeout = window.setTimeout(() => controller?.abort(), 15_000)
      try {
        const response = await fetch(
          `${import.meta.env.BASE_URL}observations.json`,
          { cache: 'no-store', signal: controller.signal },
        )
        const next = acceptSnapshot(current, await readSnapshot(response))
        if (!stopped) {
          current = next
          setDocument(next)
          setDataError(false)
        }
      } catch {
        if (!stopped) setDataError(true)
      } finally {
        window.clearTimeout(timeout)
        pending = false
      }
    }
    void refresh()
    const timer = window.setInterval(() => {
      void refresh()
    }, 60_000)
    return () => {
      stopped = true
      controller?.abort()
      window.clearInterval(timer)
    }
  }, [])
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  async function loadPlaces() {
    if (localities || placesLoading) return
    setPlacesLoading(true)
    setPlacesError(false)
    try {
      const response = await fetch(
        `${import.meta.env.BASE_URL}localities.json`,
        { signal: AbortSignal.timeout(15_000) },
      )
      if (!response.ok) throw Error('Localities unavailable')
      const text = await response.text()
      if (text.length > 6_000_000) throw Error('Locality catalogue too large')
      setLocalities(validateLocalities(JSON.parse(text)))
    } catch {
      setPlacesError(true)
    } finally {
      setPlacesLoading(false)
    }
  }

  const observations = document?.observations ?? []
  const allStations = Array.from(groupByStation(observations).values())
  const visibleStations = allStations
    .map((readings) =>
      readings.filter(
        (reading) => pollutant === 'Toți' || reading.pollutant === pollutant,
      ),
    )
    .filter((readings) => readings.length > 0)
  const recentCount = visibleStations.filter((readings) =>
    readings.some((reading) => isRecentObservation(reading, now)),
  ).length
  const key = searchText(query)
  const results = visibleStations
    .filter(
      (readings) =>
        !searchOpen ||
        !key ||
        searchText(
          `${readings[0].stationName} ${readings[0].stationId}`,
        ).includes(key),
    )
    .sort((a, b) =>
      origin
        ? distanceKm(origin, a[0]) - distanceKm(origin, b[0])
        : a[0].stationName.localeCompare(b[0].stationName, 'ro'),
    )
  const placeResults =
    searchOpen && localities ? searchLocalities(localities, query) : []
  const selectedReadings = allStations.find(
    (readings) => readings[0].stationId === selected,
  )
  const station = selectedReadings?.[0]
  const latest = latestMeasurement(
    document && { ...document, observations: visibleStations.flat() },
  )
  const lastImport = document ? latestImport(document) : null

  function selectStation(id: string) {
    cancelAddressSearch()
    setSearchOpen(false)
    const row = observations.find((reading) => reading.stationId === id)
    if (!row) return
    setSelected(id)
    setFocus({
      latitude: row.latitude,
      longitude: row.longitude,
      label: row.stationName,
    })
    setPanelOpen(true)
  }
  function choosePlace(place: Locality) {
    cancelAddressSearch()
    const point = {
      latitude: place.latitude,
      longitude: place.longitude,
      label: `${place.name}, ${place.county}`,
      precision: 'Centru de localitate · nu este o adresă exactă',
    }
    setOrigin(point)
    setFocus(point)
    setQuery(point.label)
    setSearchOpen(false)
    setSelected(null)
    setLimit(30)
    setPanelOpen(true)
  }
  function locate() {
    if (!navigator.geolocation) {
      setLocationMessage(
        'Localizarea nu este disponibilă în acest browser. Caută o localitate.',
      )
      return
    }
    setLocating(true)
    setLocationMessage('Se determină poziția…')
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const point = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          label: 'Poziția mea',
          precision: `Precizie raportată de dispozitiv: ±${Math.round(position.coords.accuracy)} m`,
        }
        cancelAddressSearch()
        setOrigin(point)
        setFocus(point)
        setQuery('')
        setSelected(null)
        setSearchOpen(false)
        setPanelOpen(true)
        setLocationMessage(
          'Stațiile sunt ordonate după distanța față de poziția ta.',
        )
        setLocating(false)
      },
      () => {
        setLocationMessage(
          'Poziția nu a putut fi obținută. Poți căuta o localitate.',
        )
        setLocating(false)
      },
      { timeout: 10_000, maximumAge: 60_000 },
    )
  }

  return (
    <main className="atlas-app" id="main-content">
      <a className="skip-link" href="#station-panel">
        Mergi la lista stațiilor
      </a>
      <header className="app-header">
        <a
          className="brand"
          href={import.meta.env.BASE_URL}
          aria-label="Atlasul Aerului, pagina principală"
        >
          <span>Atlasul Aerului</span>
        </a>
      </header>
      <div className="workspace">
        <aside
          className={`station-panel ${panelOpen ? 'is-open' : ''}`}
          id="station-panel"
          tabIndex={-1}
          aria-label="Căutare și măsurători"
        >
          <div className="search-area">
            <h1>Explorează aerul</h1>
            <label className="search-label sr-only" htmlFor="place-search">
              {addressKey
                ? 'Caută o adresă, o localitate sau o stație'
                : 'Caută o localitate sau o stație'}
            </label>
            <div className="search-field">
              {addressKey ? (
                <button
                  className="address-submit"
                  aria-label="Caută adresa"
                  disabled={addressLoading || query.trim().length < 3}
                  onClick={() => void submitAddress()}
                >
                  ⌕
                </button>
              ) : (
                <span aria-hidden="true">⌕</span>
              )}
              <input
                id="place-search"
                value={query}
                placeholder={
                  addressKey
                    ? 'Adresă, localitate sau stație'
                    : 'Localitate sau stație'
                }
                maxLength={250}
                autoComplete="off"
                aria-controls={
                  searchOpen && query.trim().length >= 2
                    ? 'search-results'
                    : undefined
                }
                onFocus={() => {
                  setSearchOpen(query !== origin?.label)
                  void loadPlaces()
                }}
                onChange={(event) => {
                  cancelAddressSearch()
                  setQuery(event.target.value)
                  setSearchOpen(true)
                  setSelected(null)
                  setLimit(30)
                  void loadPlaces()
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    cancelAddressSearch()
                    setSearchOpen(false)
                  }
                  if (
                    event.key === 'Enter' &&
                    addressKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault()
                    void submitAddress()
                  }
                }}
              />
              <button
                aria-label="Șterge căutarea și localitatea selectată"
                onClick={() => {
                  cancelAddressSearch()
                  setQuery('')
                  setOrigin(null)
                  setSearchOpen(false)
                  setSelected(null)
                  setLimit(30)
                }}
              >
                ×
              </button>
            </div>
            {searchOpen && query.trim().length >= 2 && (
              <div id="search-results" className="place-results">
                {addressLoading && <p role="status">Se caută adresa…</p>}
                {addressError && (
                  <p role="status">
                    {addressError}{' '}
                    <button
                      className="inline-button"
                      onClick={() => void submitAddress()}
                    >
                      Reîncearcă adresa
                    </button>
                  </p>
                )}
                {addressResults?.map((address, index) => (
                  <button
                    className="place-result address-result"
                    key={`${address.label}-${index}`}
                    onClick={() => chooseAddress(address)}
                  >
                    <span>
                      <strong>{address.label}</strong>
                      <small>{address.precision}</small>
                    </span>
                    <span aria-hidden="true">↗</span>
                  </button>
                ))}
                {addressResults?.length === 0 && (
                  <p role="status">
                    Nu am găsit adresa. Include strada, numărul și localitatea
                    sau alege o localitate de mai jos.
                  </p>
                )}
                {placesLoading && <p role="status">Se încarcă localitățile…</p>}
                {placesError && (
                  <p role="status">
                    Localitățile nu s-au încărcat.{' '}
                    <button
                      className="inline-button"
                      onClick={() => void loadPlaces()}
                    >
                      Reîncearcă
                    </button>
                  </p>
                )}
                {placeResults.map((place) => (
                  <button
                    key={place.id}
                    className="place-result"
                    onClick={() => choosePlace(place)}
                  >
                    <span>{place.name}</span>
                    <small>{place.county}</small>
                    <span aria-hidden="true">↗</span>
                  </button>
                ))}
                {localities &&
                  placeResults.length === 0 &&
                  addressResults === null &&
                  !addressLoading &&
                  !addressError && (
                    <p>
                      {addressKey
                        ? 'Pentru o adresă, apasă Enter sau butonul de căutare. Verifică și stațiile de mai jos.'
                        : 'Nu am găsit localitatea. Verifică și stațiile de mai jos.'}
                    </p>
                  )}
              </div>
            )}
            {addressKey && (
              <p className="address-attribution">
                Adrese: <a href="https://www.geoapify.com/">Geoapify</a> /{' '}
                <a href="https://www.openstreetmap.org/copyright">
                  OpenStreetMap
                </a>{' '}
                · căutare la Enter; textul este trimis furnizorului.
              </p>
            )}
            <button
              className="location-button"
              disabled={locating}
              onClick={locate}
            >
              <span aria-hidden="true">◎</span>
              {locating ? 'Se caută poziția…' : 'Folosește poziția mea'}
              <span aria-hidden="true">↗</span>
            </button>
            {locationMessage && (
              <p className="helper-text" role="status">
                {locationMessage}
              </p>
            )}
          </div>
          <div className="filter-area">
            <div
              className="pollutant-filters"
              aria-label="Filtrează după poluant"
            >
              {pollutants.map((value) => (
                <button
                  key={value}
                  aria-pressed={pollutant === value}
                  className={pollutant === value ? 'active' : ''}
                  onClick={() => {
                    setPollutant(value)
                    setLimit(30)
                  }}
                >
                  {pollutantLabels[value]}
                </button>
              ))}
            </div>
          </div>
          <div className="pollutant-context" aria-live="polite">
            {origin && (
              <p className="point-provenance">
                <strong>{origin.label}</strong>
                <span>{origin.precision ?? 'Poziție selectată'}</span>
                <span>
                  Estimarea pentru acest punct nu este încă disponibilă.
                  Stațiile apropiate sunt afișate ca referință; distanța nu
                  confirmă acoperirea.
                </span>
              </p>
            )}
            {pollutant === 'Toți' ? (
              <section
                className="quality-overview"
                aria-label="Calitatea aerului"
              >
                <span>Calitatea aerului</span>
                <strong>Indice european</strong>
                <p>
                  Calculul indicelui este în pregătire. Concentrațiile măsurate
                  sunt disponibile la fiecare stație.
                </p>
              </section>
            ) : (
              <p>
                <strong>{pollutantNames[pollutant]}</strong>
                {pollutant === 'PM2.5'
                  ? ' · particule de cel mult 2,5 µm'
                  : pollutant === 'PM10'
                    ? ' · particule de cel mult 10 µm'
                    : ''}
              </p>
            )}
          </div>
          <button
            className="mobile-panel-toggle"
            aria-expanded={panelOpen}
            aria-controls="panel-content"
            onClick={() => setPanelOpen(!panelOpen)}
          >
            {station ? station.stationName : 'Stații și măsurători'}{' '}
            <span aria-hidden="true">{panelOpen ? '−' : '+'}</span>
          </button>
          <div className="panel-content" id="panel-content">
            {station && selectedReadings ? (
              <section
                className="station-detail"
                aria-label={`Măsurători ${station.stationName}`}
              >
                <button
                  className="back-button"
                  onClick={() => {
                    setSelected(null)
                    requestAnimationFrame(() =>
                      globalThis.document
                        .getElementById('results-heading')
                        ?.focus({ preventScroll: true }),
                    )
                  }}
                >
                  ← Înapoi la stații
                </button>
                <div className="detail-heading">
                  <div>
                    <div className="eyebrow">STAȚIE DE MONITORIZARE</div>
                    <h2 ref={detailHeading} tabIndex={-1}>
                      {station.stationName}
                    </h2>
                    <span className="station-code">
                      {station.stationId} · EEA
                    </span>
                  </div>
                  <span className="detail-symbol" aria-hidden="true">
                    ◎
                  </span>
                </div>
                {origin && (
                  <p className="helper-text">
                    La {formatValue(distanceKm(origin, station))} km de{' '}
                    {origin.label}. Distanță în linie dreaptă.
                  </p>
                )}
                <div className="measurement-list">
                  {selectedReadings
                    .filter(
                      (reading) =>
                        pollutant === 'Toți' || reading.pollutant === pollutant,
                    )
                    .map((reading) => (
                      <article
                        className="measurement"
                        key={reading.samplingPointId}
                      >
                        <div className="measurement-heading">
                          <span>{pollutantLabels[reading.pollutant]}</span>
                          <span
                            className={`age-badge ${isRecentObservation(reading, now) ? 'recent' : 'old'}`}
                          >
                            {isRecentObservation(reading, now)
                              ? 'Recent'
                              : 'Date vechi'}
                          </span>
                        </div>
                        <div className="measurement-value">
                          {formatValue(reading.value)}{' '}
                          <small>
                            {reading.unit === 'mg.m-3' ? 'mg/m³' : 'µg/m³'}
                          </small>
                        </div>
                        <p className="pollutant-name">
                          {pollutantNames[reading.pollutant]}
                        </p>
                        <p className="age-line">
                          {measurementAge(reading, now)}
                        </p>
                        <details>
                          <summary>Interval și proveniență</summary>
                          <dl>
                            <dt>
                              Interval{' '}
                              {Date.parse(reading.observedTo) -
                                Date.parse(reading.observedFrom) ===
                              86_400_000
                                ? 'zilnic'
                                : 'orar'}
                            </dt>
                            <dd>
                              {formatTime(reading.observedFrom)} –{' '}
                              {formatTime(reading.observedTo)}
                            </dd>
                            <dt>Raportat</dt>
                            <dd>{formatTime(reading.reportedAt)}</dd>
                            <dt>Importat</dt>
                            <dd>{formatTime(reading.ingestedAt)}</dd>
                            <dt>Validare</dt>
                            <dd>
                              {reading.status === 'validated'
                                ? 'Date validate'
                                : 'Date preliminare'}
                            </dd>
                          </dl>
                          <a
                            href={reading.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Fișierul sursă EEA ↗
                          </a>
                        </details>
                      </article>
                    ))}
                </div>
                {pollutant !== 'Toți' &&
                  !selectedReadings.some(
                    (reading) => reading.pollutant === pollutant,
                  ) && (
                    <p className="empty-state">
                      Stația nu are o valoare disponibilă pentru acest poluant.
                    </p>
                  )}
              </section>
            ) : (
              <section
                className="station-results"
                aria-label="Stații disponibile"
              >
                <div className="results-heading">
                  <div>
                    <h2 id="results-heading" tabIndex={-1}>
                      {origin ? 'Stații apropiate' : 'Stații disponibile'}
                    </h2>
                    <p>{origin ? origin.label : 'Ultimele valori cunoscute'}</p>
                  </div>
                  <span className="count-badge">{results.length}</span>
                </div>
                {!document && (
                  <div className="empty-state" role="status">
                    {dataError
                      ? 'Măsurătorile nu s-au încărcat. Reîncercăm automat într-un minut.'
                      : 'Se încarcă măsurătorile…'}
                  </div>
                )}
                {document && results.length === 0 && (
                  <div className="empty-state">
                    Nu am găsit stații pentru căutarea și poluantul selectat.
                    Încearcă o localitate sau alt poluant.
                  </div>
                )}
                {results.slice(0, limit).map((readings) => {
                  const row = readings[0]
                  const fresh = readings.filter((reading) =>
                    isRecentObservation(reading, now),
                  ).length
                  const latestRow = readings.reduce((a, b) =>
                    Date.parse(a.observedTo) > Date.parse(b.observedTo) ? a : b,
                  )
                  return (
                    <button
                      className="station-result"
                      key={row.stationId}
                      onClick={() => selectStation(row.stationId)}
                    >
                      <span
                        className={`station-dot ${fresh ? 'recent' : 'old'}`}
                        aria-hidden="true"
                      />
                      <span className="station-result-text">
                        <strong>{row.stationName}</strong>
                        <small>
                          {row.stationId}
                          {origin &&
                            ` · ${formatValue(distanceKm(origin, row))} km`}
                        </small>
                        <span>
                          {fresh
                            ? `${fresh}/${readings.length} valori recente`
                            : measurementAge(latestRow, now)}
                        </span>
                      </span>
                      <span className="station-result-end">
                        <small>
                          {readings
                            .map(
                              (reading) => pollutantLabels[reading.pollutant],
                            )
                            .slice(0, 3)
                            .join(' · ')}
                          {readings.length > 3 && ` +${readings.length - 3}`}
                        </small>
                        <span aria-hidden="true">↗</span>
                      </span>
                    </button>
                  )
                })}
                {results.length > limit && (
                  <button
                    className="more-button"
                    onClick={() => setLimit(limit + 30)}
                  >
                    Arată mai multe stații
                  </button>
                )}
              </section>
            )}
          </div>
          <details className="source-info">
            <summary>Despre date și surse</summary>
            <div>
              <h2>Ce afișează atlasul</h2>
              <p>
                Ultima concentrație disponibilă la fiecare stație pentru PM₂.₅,
                PM₁₀, NO₂, O₃, SO₂ și CO. Sunt măsurători punctuale; între
                stații nu estimăm încă valorile.
              </p>
              <h3>Ora și unitățile</h3>
              <p>
                Fiecare valoare are propriul interval orar sau zilnic. Ora este
                afișată pentru România. Concentrațiile sunt în µg/m³, iar CO în
                mg/m³. Importarea recentă a unui fișier nu face măsurătorile mai
                noi.
              </p>
              <h3>Recent sau vechi</h3>
              <p>
                O măsurătoare este recentă dacă intervalul raportat a început și
                este în curs sau s-a încheiat cu mai puțin de 6 ore în urmă.
                Valorile mai vechi rămân vizibile cu vârsta lor. Datele
                preliminare pot fi corectate de furnizor.
              </p>
              <h3>Calitatea aerului la o adresă</h3>
              <p>
                O adresă identifică un punct, nu o măsurătoare. Alegerea unei
                stații pentru acel punct cere o zonă de reprezentativitate
                verificată. Momentan afișăm stațiile apropiate și distanța; nu
                avem încă aceste zone sau o estimare modelată pentru adresă.
              </p>
              <h3>Indicele de calitate</h3>
              <p>
                Vom folosi indicele european: o categorie bazată pe poluantul cu
                situația cea mai nefavorabilă, pentru aceeași oră. Nu calculăm o
                medie între poluanți sau între stațiile unei localități.
                Calculul și verificarea completitudinii nu sunt încă
                implementate.
              </p>
              <h3>Adrese și precizie</h3>
              <p>
                {addressKey
                  ? 'Căutarea unei adrese pornește la Enter sau la apăsarea butonului de căutare. Textul este trimis către Geoapify; rezultatul poate fi o clădire, o stradă sau o localitate. Precizia potrivirii este indicată înainte de selecție.'
                  : 'Catalogul actual găsește localități, nu străzi și numere. Căutarea adreselor prin Geoapify va fi activată după configurarea serviciului.'}
              </p>
              <h3>Surse și licențe</h3>
              <p>
                Măsurători:{' '}
                <a href="https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d">
                  EEA
                </a>
                ; localități: <a href="https://www.geonames.org/">GeoNames</a> —{' '}
                <a href="https://creativecommons.org/licenses/by/4.0/">
                  CC BY 4.0
                </a>
                . Atlasul selectează și normalizează măsurătorile; furnizorii nu
                aprobă aplicația.
              </p>
              <p>
                <a href="https://airindex.eea.europa.eu/AQI/">
                  Metodologia indicelui european ↗
                </a>
              </p>
            </div>
          </details>
        </aside>
        <section className="map-area" aria-label="Harta stațiilor din România">
          <div className="map-frame">
            <MapContainer
              bounds={romaniaBounds}
              zoomSnap={0.25}
              zoomDelta={0.5}
              boundsOptions={{ padding: [30, 30] }}
              className="atlas-map"
              zoomControl={false}
              zoomAnimation={false}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
                eventHandlers={{
                  tileerror: () => setTilesFailed(true),
                  loading: () => setTilesFailed(false),
                }}
                noWrap
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <StationMarkers
                stations={visibleStations}
                selected={selected}
                now={now}
                onSelect={selectStation}
              />
              <MapPosition focus={focus} />
              {origin && (
                <Marker
                  position={[origin.latitude, origin.longitude]}
                  title={origin.label}
                  icon={divIcon({
                    className: 'origin-marker',
                    html: '<span>+</span>',
                    iconSize: [24, 24],
                    iconAnchor: [12, 12],
                  })}
                >
                  <Popup>{origin.label}</Popup>
                </Marker>
              )}
              <MapControls />
            </MapContainer>
          </div>
          <section className="time-panel" aria-label="Timpul măsurătorilor">
            <div className="time-summary">
              <div>
                <h2>Ultimele măsurători</h2>
                <p>
                  {latest === null
                    ? 'Se încarcă datele…'
                    : `Cea mai nouă: ${formatTime(latest)}`}
                </p>
              </div>
              {document && (
                <span
                  className={`time-state ${recentCount === 0 ? 'is-stale' : ''}`}
                  role="status"
                >
                  {recentCount === 0
                    ? 'Date vechi'
                    : `${recentCount} stații cu date recente`}
                </span>
              )}
            </div>
            <details className="data-details">
              <summary>Legenda hărții</summary>
              <div className="data-context">
                <p>
                  <strong>Măsurători EEA</strong> · {visibleStations.length}{' '}
                  stații în selecție, {recentCount} cu cel puțin o valoare
                  recentă.
                </p>
                <div className="freshness-key">
                  <span>
                    <i className="station-dot recent" aria-hidden="true" />
                    Are date recente
                  </span>
                  <span>
                    <i className="station-dot old" aria-hidden="true" />
                    Doar date vechi
                  </span>
                </div>
                <p>
                  Culoarea punctelor indică prospețimea, nu nivelul poluării. O
                  valoare recentă nu înseamnă că toate măsurătorile stației sunt
                  recente.
                </p>
                <p>
                  Data de mai sus este sfârșitul celui mai nou interval din
                  selecție. Observațiile au momente diferite; valorile vechi
                  rămân vizibile cu vechimea lor.
                </p>
              </div>
            </details>
            {(dataError || tilesFailed) && (
              <p className="data-error" role="status">
                {dataError &&
                  (document
                    ? 'Actualizarea nu a reușit. Păstrăm valorile acceptate și reîncercăm automat. '
                    : 'Măsurătorile nu s-au încărcat. Reîncercăm automat într-un minut. ')}
                {tilesFailed &&
                  'Fundalul hărții nu s-a încărcat complet. Lista stațiilor rămâne disponibilă.'}
              </p>
            )}
          </section>
        </section>
      </div>
      <footer className="app-footer">
        <span>
          Date{' '}
          <a href="https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d">
            EEA
          </a>{' '}
          · CC BY 4.0 <span aria-hidden="true">/</span> Localități{' '}
          <a href="https://www.geonames.org/">GeoNames</a> · CC BY 4.0
        </span>
        <span>
          {lastImport === null
            ? 'Date cu sursa la vedere.'
            : `Ultimul import acceptat: ${formatTime(lastImport)}`}
        </span>
      </footer>
    </main>
  )
}

function MapControls() {
  const map = useMap()
  return (
    <div className="map-controls">
      <button aria-label="Mărește harta" onClick={() => map.zoomIn()}>
        +
      </button>
      <button aria-label="Micșorează harta" onClick={() => map.zoomOut()}>
        −
      </button>
      <button
        className="reset-map"
        onClick={() => map.fitBounds(romaniaBounds)}
      >
        România
      </button>
    </div>
  )
}
export default App
