export type AirQualityObservation = {
    source: string
    stationId: string
    stationName: string
    samplingPointId: string
    locality: string
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

export const craiovaObservation: AirQualityObservation = {
    source: "EEA",
    stationId: "RO0080A",
    stationName: "DJ-3",
    samplingPointId: "RO/SPO-RO0080A_00008_100",
    locality: "Craiova",
    pollutant: "NO2",
    value: 22.63848,
    unit: "ug.m-3",
    latitude: 44.3268,
    longitude: 23.7787,
    observedFrom: "2026-09-11T06:00:00+01:00",
    observedTo: "2026-09-11T07:00:00+01:00",
    reportedAt: "2026-09-11T06:42:34+01:00",
    ingestedAt: "2026-09-11T06:12:46Z",
    validity: 1,
    verification: 3,
    sourceUrl: "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet",
    status: 'preliminary'
}

export const bucharestObservation: AirQualityObservation = {
    source: "EEA",
    stationId: "RO0065A",
    stationName: "B-1",
    samplingPointId: "RO/SPO-RO0065A_00008_100",
    locality: "București",
    pollutant: "NO2",
    value: 32.34278,
    unit: "ug.m-3",
    latitude: 44.4473,
    longitude: 26.0367,
    observedFrom: "2026-09-11T07:00:00+01:00",
    observedTo: "2026-09-11T08:00:00+01:00",
    reportedAt: "2026-09-11T08:02:34+01:00",
    ingestedAt: "2026-09-11T07:37:39Z",
    validity: 1,
    verification: 3,
    sourceUrl: "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0065A_00008_100.parquet",
    status: 'preliminary'
}

export const bucharestPm10Observation: AirQualityObservation = {
    source: "EEA",
    stationId: "RO0065A",
    stationName: "B-1",
    samplingPointId: "RO/SPO-RO0065A_00005_101",
    locality: "București",
    pollutant: "PM10",
    value: 21.50805,
    unit: "ug.m-3",
    latitude: 44.4473,
    longitude: 26.0367,
    observedFrom: "2026-09-11T07:00:00+01:00",
    observedTo: "2026-09-11T08:00:00+01:00",
    reportedAt: "2026-09-11T08:02:34+01:00",
    ingestedAt: "2026-09-11T07:43:14Z",
    validity: 1,
    verification: 3,
    sourceUrl: "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0065A_00005_101.parquet",
    status: 'preliminary'
}

export const observations: AirQualityObservation[] = [
    craiovaObservation,
    bucharestObservation,
    bucharestPm10Observation
]

export const observationsByStation = new Map<string, AirQualityObservation[]>()
for (const observation of observations) {
    const stationObservations = observationsByStation.get(observation.stationId) ?? []
    stationObservations.push(observation)
    observationsByStation.set(observation.stationId, stationObservations)
}