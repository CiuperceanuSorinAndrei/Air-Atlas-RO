export type AirQualityObservation = {
  source: 'EEA'
  stationId: string
  stationName: string
  samplingPointId: string
  pollutant: string
  value: number
  unit: 'ug.m-3'
  latitude: number
  longitude: number
  observedFrom: string
  observedTo: string
  reportedAt: string
  ingestedAt: string
  aggregationType?: 'hour' | 'day'
  sourceRecordId?: string | null
  dataCapture?: number | null
  validity: number
  verification: number
  sourceUrl: string
  status: 'preliminary' | 'validated'
}
export type ObservationDocument = {
  observations: AirQualityObservation[]
  importSummary: { attempted: number; imported: number; skipped: number; failed: number }
}
const names: Record<string, string> = { '00001': 'SO2', '00005': 'PM10', '00007': 'O3', '00008': 'NO2', '06001': 'PM2.5' }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Expected an object')
  return value as Record<string, unknown>
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 512) throw Error('Invalid text')
  return value
}
function numeric(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw Error('Invalid number')
  return value
}
function timestamp(value: unknown): number {
  const string = text(value)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(string)) throw Error('Missing timestamp offset')
  const time = Date.parse(string)
  if (!Number.isFinite(time)) throw Error('Invalid timestamp')
  return time
}
export function validateObservationDocument(value: unknown): ObservationDocument {
  const document = object(value), summary = object(document.importSummary)
  if (!Array.isArray(document.observations) || document.observations.length === 0) throw Error('Empty observations')
  for (const key of ['attempted', 'imported', 'skipped', 'failed']) {
    const count = numeric(summary[key])
    if (!Number.isSafeInteger(count) || count < 0) throw Error('Invalid import count')
  }
  if (summary.imported !== document.observations.length || summary.attempted !== Number(summary.imported) + Number(summary.skipped) + Number(summary.failed)) throw Error('Import count mismatch')
  const pairs = new Set<string>()
  const metadata = new Map<string, string>()
  for (const item of document.observations) {
    const row = object(item)
    const identity = /^RO\/SPO-(RO[A-Z0-9]{1,6})_(\d{5})_(\d+)$/.exec(text(row.samplingPointId))
    if (!identity || identity[1] !== row.stationId || names[identity[2]] !== row.pollutant || row.source !== 'EEA') throw Error('Invalid series identity')
    const source = new URL(text(row.sourceUrl))
    if (source.protocol !== 'https:' || source.hostname !== 'eeadmz1batchservice02.blob.core.windows.net' || source.username || source.password || source.port || source.search || source.hash || source.pathname !== `/airquality-p/RO/SPO-${identity[1]}_${identity[2]}_${identity[3]}.parquet`) throw Error('Invalid source URL')
    text(row.stationName)
    const location = JSON.stringify([row.stationName, row.latitude, row.longitude])
    const station = String(row.stationId)
    if (metadata.has(station) && metadata.get(station) !== location) throw Error('Conflicting station metadata')
    metadata.set(station, location)
    if (Math.abs(numeric(row.latitude)) > 90 || Math.abs(numeric(row.longitude)) > 180) throw Error('Invalid coordinates')
    numeric(row.value)
    if (row.unit !== 'ug.m-3' || ![1, 2, 3, 4].includes(numeric(row.validity)) || ![1, 2, 3].includes(numeric(row.verification)) || row.status !== (row.verification === 1 ? 'validated' : 'preliminary')) throw Error('Invalid quality metadata')
    const start = timestamp(row.observedFrom), end = timestamp(row.observedTo), reported = timestamp(row.reportedAt), ingested = timestamp(row.ingestedAt)
    const aggregation = row.aggregationType ?? (end - start === 3_600_000 ? 'hour' : 'day')
    if (!['hour', 'day'].includes(String(aggregation)) || end - start !== (aggregation === 'hour' ? 3_600_000 : 86_400_000) || start > ingested || reported > ingested) throw Error('Invalid interval')
    if (row.sourceRecordId != null) text(row.sourceRecordId)
    if (row.dataCapture != null && (numeric(row.dataCapture) < 0 || numeric(row.dataCapture) > 100)) throw Error('Invalid data capture')
    const pair = `${row.stationId}:${row.pollutant}`
    if (pairs.has(pair)) throw Error('Duplicate station/pollutant')
    pairs.add(pair)
  }
  // The assertion is reached only after the public document's fields have been checked.
  return document as unknown as ObservationDocument
}
export function groupByStation(observations: AirQualityObservation[]): Map<string, AirQualityObservation[]> {
  const stations = new Map<string, AirQualityObservation[]>()
  for (const observation of observations) {
    const readings = stations.get(observation.stationId) ?? []
    readings.push(observation)
    stations.set(observation.stationId, readings)
  }
  return stations
}
export function latestImport(document: ObservationDocument): number {
  return Math.max(...document.observations.map(row => Date.parse(row.ingestedAt)))
}
export function acceptSnapshot(previous: ObservationDocument | null, next: ObservationDocument): ObservationDocument {
  if (previous && latestImport(next) < latestImport(previous)) throw Error('Older snapshot received')
  return next
}
