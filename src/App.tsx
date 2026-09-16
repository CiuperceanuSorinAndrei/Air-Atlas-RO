import './App.css'
import { observationsByStation, importSummary } from './airQualityObservation'
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet'

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('ro-RO', { timeZone: 'Europe/Bucharest' })
}

const statusLabels = {
  preliminary: 'Date preliminare',
  validated: 'Date validate',
  modelled: 'Date modelate'
}

function isObservationStale(observedTo: string): boolean {
  const observedToMilliseconds = new Date(observedTo).getTime()
  const currentMilliseconds = Date.now()
  const ageMilliseconds = currentMilliseconds - observedToMilliseconds
  const staleThresholdMilliseconds = 24 * 60 * 60 * 1000
  return ageMilliseconds >= staleThresholdMilliseconds
}

function App() {
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
      <MapContainer
        center={[45.9432, 24.9668]}
        zoom={7}
        style={{ height: '60vh', width: '100%' }}
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {Array.from(observationsByStation.values()).map((stationObservations) => {
          const station = stationObservations[0]

          return (
            <Marker
              key={station.stationId}
              position={[station.latitude, station.longitude]}
            >
              <Popup>
                {station.stationName}
                <br />
                {stationObservations.map((reading) => (
                  <div key={reading.samplingPointId}>
                    {reading.pollutant}: {reading.value}{' '}
                    {reading.unit}
                    <br />
                    Interval:{' '}
                    {formatDateTime(reading.observedFrom)}
                    {' – '}
                    {formatDateTime(reading.observedTo)}
                    <br />
                    {isObservationStale(reading.observedTo) ? 'Măsurare veche' : 'Măsurare recentă'}
                    <br />
                    Statut: {statusLabels[reading.status]}
                    <br />
                    Sursă:{' '}
                    <a
                      href={reading.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {reading.source}
                    </a>
                  </div>
                ))}
              </Popup>
            </Marker>
          )
        })}
      </MapContainer>
    </main>
  )
}

export default App
