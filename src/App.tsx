import './App.css'
import { useEffect, useState } from 'react'
import { divIcon } from 'leaflet'
import { observations, observationsByStation, importSummary, type AirQualityObservation } from './airQualityObservation'
import { isRecentObservation } from './observationFreshness'
import { MapContainer, TileLayer, Marker, Popup, useMapEvents } from 'react-leaflet'

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('ro-RO', { timeZone: 'Europe/Bucharest' })
}

const statusLabels = {
  preliminary: 'Date preliminare',
  validated: 'Date validate',
  modelled: 'Date modelate'
}

const initialNow = Date.now()
const lastImport = observations.reduce(
  (latest, reading) => reading.ingestedAt > latest ? reading.ingestedAt : latest,
  ''
)
const stationIcon = divIcon({ className: 'station-marker', iconSize: [12, 12], iconAnchor: [6, 6] })

function StationMarkers({ stations, now }: { stations: AirQualityObservation[][], now: number }) {
  const [zoom, setZoom] = useState(7)
  const map = useMapEvents({ zoomend: () => setZoom(map.getZoom()) })
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
        html: String(group.length),
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      })

      return (
        <Marker
          key={group.map((readings) => readings[0].stationId).join(',')}
          position={[latitude, longitude]}
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
        icon={stationIcon}
      >
        <Popup>
          {station.stationName}
          <br />
          {stationObservations.map((reading) => (
            <div key={reading.samplingPointId}>
              {reading.pollutant}: {reading.value} {reading.unit}
              <br />
              Interval: {formatDateTime(reading.observedFrom)}{' – '}{formatDateTime(reading.observedTo)}{Date.parse(reading.observedTo) > now && ' (în curs)'}
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
        <p>
          Eșantion EEA: {importSummary.imported} din {importSummary.attempted}{' '}
          serii importate · {importSummary.skipped} fără observații valide ·{' '}
          {importSummary.failed} eșuate
        </p>
      </div>
      {recentStations.length === 0 && (
        <p>
          Nu sunt disponibile măsurători recente (din ultimele 6 ore).{' '}
          {lastImport && `Ultimul import: ${formatDateTime(lastImport)}.`}
        </p>
      )}
      <MapContainer
        center={[45.9432, 24.9668]}
        zoom={7}
        style={{ height: '60vh', width: '100%' }}
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <StationMarkers stations={recentStations} now={now} />
      </MapContainer>
    </main>
  )
}

export default App
