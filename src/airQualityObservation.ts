import rawObservation from "./data/observations.json"

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

export const observations = rawObservation as AirQualityObservation[]

export const observationsByStation = new Map<string, AirQualityObservation[]>()
for (const observation of observations) {
    const stationObservations = observationsByStation.get(observation.stationId) ?? []
    stationObservations.push(observation)
    observationsByStation.set(observation.stationId, stationObservations)
}