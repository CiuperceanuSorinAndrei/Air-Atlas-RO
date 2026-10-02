import './App.css'
import { useEffect, useState } from 'react'
import { divIcon } from 'leaflet'
import { acceptSnapshot, groupByStation, latestImport, type ObservationDocument, type AirQualityObservation } from './airQualityObservation'
import { readSnapshot } from './fetchSnapshot'
import { isRecentObservation } from './observationFreshness'
import { MapContainer, TileLayer, Marker, Popup, useMapEvents, useMap } from 'react-leaflet'

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('ro-RO', { timeZone: 'Europe/Bucharest' })
}

const statusLabels = {
  preliminary: 'Date preliminare',
  validated: 'Date validate',
  modelled: 'Date modelate'
}

const initialNow = Date.now()
const stationIcon = divIcon({ className: 'station-marker', iconSize: [12, 12], iconAnchor: [6, 6] })

function StationMarkers({ stations, now }: { stations: AirQualityObservation[][], now: number }) {
  const map = useMap()
  const [zoom, setZoom] = useState(() => map.getZoom())
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) })
  const groups = new Map<string, AirQualityObservation[][]>()

  for (const readings of stations) {
    const station = readings[0]
    const point = map.project([station.latitude, station.longitude], zoom)
    const key = zoom >= 12
      ? station.stationId
      : `${Math.floor(point.x / 32)}:${Math.floor(point.y / 32)}`
    const group = groups.get(key) ?? []
    group.push(readings)
    groups.set(key, group)
  }

  return Array.from(groups.values()).map((group) => {
    if (group.length > 1) {
      const latitude = group.reduce((total, readings) => total + readings[0].latitude, 0) / group.length
      const longitude = group.reduce((total, readings) => total + readings[0].longitude, 0) / group.length
      const clusterIcon = divIcon({
        className: 'station-cluster',
        html: `<span role="img" aria-label="${group.length} stații. Mărește harta.">${group.length}</span>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      })

      return (
        <Marker
          key={group.map((readings) => readings[0].stationId).join(',')}
          position={[latitude, longitude]}
          title={`${group.length} stații. Activați pentru a mări harta.`}
          icon={clusterIcon}
          eventHandlers={{ click: () => map.flyTo([latitude, longitude], Math.min(zoom + 2, 12)) }}
        />
      )
    }

    const stationObservations = group[0]
    const station = stationObservations[0]
    return (
      <Marker
        key={station.stationId}
        position={[station.latitude, station.longitude]}
        title={`${station.stationName} (${station.stationId})`}
        icon={stationIcon}
      >
        <Popup>
          {station.stationName}
          <br />
          {stationObservations.map((reading) => (
            <div key={reading.samplingPointId}>
              {reading.pollutant}: {reading.value} {reading.unit}
              <br />
              Interval {Date.parse(reading.observedTo) - Date.parse(reading.observedFrom) === 86_400_000 ? 'zilnic' : 'orar'}: {formatDateTime(reading.observedFrom)}{' – '}{formatDateTime(reading.observedTo)}{Date.parse(reading.observedTo) > now && ' (în curs)'}
              <br />
              Raportat: {formatDateTime(reading.reportedAt)}
              <br />
              Statut: {statusLabels[reading.status]}
              <br />
              Sursă:{' '}
              <a href={reading.sourceUrl} target="_blank" rel="noreferrer">
                {reading.source}
              </a>
            </div>
          ))}
        </Popup>
      </Marker>
    )
  })
}

function App() {
  const [now, setNow] = useState(initialNow)
  const [document, setDocument] = useState<ObservationDocument | null>(null)
  const [dataError, setDataError] = useState(false)
  const [tilesFailed, setTilesFailed] = useState(false)
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
        const response = await fetch(`${import.meta.env.BASE_URL}observations.json`, { cache: 'no-store', signal: controller.signal })
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
    const timer = window.setInterval(() => { void refresh() }, 60_000)
    return () => { stopped = true; controller?.abort(); window.clearInterval(timer) }
  }, [])
  const observationsByStation = groupByStation(document?.observations ?? [])
  const importSummary = document?.importSummary
  const lastImport = document ? new Date(latestImport(document)).toISOString() : ''

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const recentStations = Array.from(observationsByStation.values())
    .map((stationObservations) => stationObservations.filter((reading) => isRecentObservation(reading, now)))
    .filter((stationObservations) => stationObservations.length > 0)

  return (
    <main id="center">
      <div>
        <h1>Atlasul Aerului</h1>
        {importSummary && <p>
          Eșantion EEA: {importSummary.imported} din {importSummary.attempted}{' '}
          serii importate · {importSummary.skipped} fără observații valide ·{' '}
          {importSummary.failed} eșuate
          {lastImport && ` · Ultimul import: ${formatDateTime(lastImport)}.`}
        </p>}
      </div>
      {!document && !dataError && <p role="status">Se încarcă măsurătorile…</p>}
      {dataError && <p role="status">Actualizarea datelor nu a reușit. Se reîncearcă automat într-un minut; măsurătorile vechi expiră în continuare.</p>}
      {tilesFailed && <p role="status">Fundalul hărții nu este disponibil. Măsurătorile rămân accesibile în lista de mai jos.</p>}
      {document && recentStations.length === 0 && (
        <p>
          Nu sunt disponibile măsurători recente (din ultimele 6 ore).
        </p>
      )}
      <MapContainer
        bounds={[[43.5, 20], [48.4, 30.2]]}
        boundsOptions={{ padding: [12, 12] }}
        style={{ height: '60vh', width: '100%' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
          eventHandlers={{ tileerror: () => setTilesFailed(true), tileload: () => setTilesFailed(false) }}
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <StationMarkers stations={recentStations} now={now} />
      </MapContainer>
      {recentStations.length > 0 && <details className="station-list">
        <summary>Lista măsurătorilor recente ({recentStations.length} stații)</summary>
        <ul>{recentStations.map(readings => <li key={readings[0].stationId}>
          <strong>{readings[0].stationName} ({readings[0].stationId})</strong>
          <ul>{readings.map(reading => <li key={reading.samplingPointId}>{reading.pollutant}: {reading.value} {reading.unit} · {formatDateTime(reading.observedFrom)} – {formatDateTime(reading.observedTo)} · {statusLabels[reading.status]}</li>)}</ul>
        </li>)}</ul>
      </details>}
      <footer>
        <p>Date: <a href="https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d">European Environment Agency</a> · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Atlasul Aerului selectează, normalizează și filtrează datele; EEA nu aprobă această aplicație.</p>
        <p>Acoperire parțială NO2, PM10, PM2.5, SO2, O3 și CO, date preliminare și validate. Actualizarea programată poate întârzia. Fereastra de 6 ore indică recența, nu un indice de sănătate.</p>
      </footer>
    </main>
  )
}

export default App
