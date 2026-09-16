import rawDocument from "./data/observations.json"

export type AirQualityObservation = {
    source: string
    stationId: string
    stationName: string
    samplingPointId: string
    pollutant: string
    value: number
    unit: string
    latitude: number
    longitude: number
    observedFrom: string
    observedTo: string
    reportedAt: string
    ingestedAt: string
    validity: number
    verification: number
    sourceUrl: string
    status: 'preliminary' | 'validated' | 'modelled'
}

type ImportSummary = {
    attempted: number
    imported: number
    skipped: number
    failed: number
}

type ObservationDocument = {
    observations: AirQualityObservation[]
    importSummary: ImportSummary
}

const observationDocument = rawDocument as ObservationDocument
export const observations = observationDocument.observations
export const importSummary = observationDocument.importSummary

export const observationsByStation = new Map<string, AirQualityObservation[]>()
for (const observation of observations) {
    const stationObservations = observationsByStation.get(observation.stationId) ?? []
    stationObservations.push(observation)
    observationsByStation.set(observation.stationId, stationObservations)
}
