export type PilotObservation = {
  observedFrom: string
  observedTo: string
  value: string
  source: 'EEA' | 'RNMCA'
  reportedAt: string | null
  ingestedAt: string
  validationState: string | null
  sourceValidity: string | null
  sourceVerification: string | null
  normalizationRule: string | null
  temporalStatus: string | null
}
export type PilotSource = { code: 'EEA' | 'RNMCA'; attributionText: string; licenceUrl: string }
export type PilotHistory = {
  windowStart: string
  windowEnd: string
  sources: PilotSource[]
  observations: PilotObservation[]
  missingIntervals: { observedFrom: string; observedTo: string }[]
}
export type PilotHour = { observedFrom: string; observedTo: string; observation: PilotObservation | null }
export const pilotWindow = { from: '2026-10-10T18:00:00Z', to: '2026-10-10T20:00:00Z' }
const hour = 3_600_000
const maximumBytes = 200_000

function instant(value: unknown): number {
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) throw Error('Invalid timestamp')
  const time = Date.parse(value)
  if (!Number.isFinite(time)) throw Error('Invalid timestamp')
  return time
}
function optionalText(value: unknown): string | null {
  if (value !== null && typeof value !== 'string') throw Error('Invalid text')
  return value
}
function interval(row: Record<string, unknown>, start: number, end: number, seen: Set<number>): void {
  const from = instant(row.observedFrom), to = instant(row.observedTo)
  if (to - from !== hour || to % hour !== 0 || to <= start || to > end || seen.has(to)) throw Error('Invalid interval')
  seen.add(to)
}

export function validatePilotHistory(value: unknown, from: string, to: string): PilotHistory {
  const document = value as Record<string, unknown>
  if (!document || typeof document !== 'object' || document.schemaVersion !== 1 || document.stationCode !== 'DJ-1' ||
      document.pollutant !== 'NO2' || document.unit !== 'ug.m-3' || document.aggregationType !== 'hour' ||
      document.gapCause !== 'unknown' || !Array.isArray(document.sources) || !Array.isArray(document.observations) ||
      !Array.isArray(document.missingIntervals)) throw Error('Invalid history')
  const start = instant(document.windowStart), end = instant(document.windowEnd)
  if (start !== instant(from) || end !== instant(to) || start % hour !== 0 || end % hour !== 0 || end <= start || (end - start) / hour > 168) throw Error('Invalid window')
  const codes = new Set<string>()
  for (const source of document.sources as Record<string, unknown>[]) {
    if (!source || (source.code !== 'EEA' && source.code !== 'RNMCA') || codes.has(source.code) ||
        typeof source.attributionText !== 'string' || !source.attributionText.trim() ||
        typeof source.licenceUrl !== 'string' || new URL(source.licenceUrl).protocol !== 'https:') throw Error('Invalid source')
    codes.add(source.code)
  }
  const seen = new Set<number>()
  for (const row of document.observations as Record<string, unknown>[]) {
    if (!row || typeof row.value !== 'string' || !/^-?\d+(\.\d+)?$/.test(row.value) || typeof row.source !== 'string' || !codes.has(row.source)) throw Error('Invalid observation')
    interval(row, start, end, seen)
    if (row.reportedAt !== null) instant(row.reportedAt)
    instant(row.ingestedAt)
    for (const key of ['validationState', 'sourceValidity', 'sourceVerification', 'normalizationRule', 'temporalStatus']) optionalText(row[key])
  }
  for (const row of document.missingIntervals as Record<string, unknown>[]) {
    if (!row) throw Error('Invalid gap')
    interval(row, start, end, seen)
  }
  if (seen.size !== (end - start) / hour) throw Error('History does not cover its window')
  return document as unknown as PilotHistory
}

export function pilotHours(history: PilotHistory): PilotHour[] {
  return [
    ...history.observations.map((observation) => ({ observedFrom: observation.observedFrom, observedTo: observation.observedTo, observation })),
    ...history.missingIntervals.map((gap) => ({ ...gap, observation: null })),
  ].sort((a, b) => Date.parse(a.observedTo) - Date.parse(b.observedTo))
}

export async function fetchPilotHistory(baseUrl: string, signal: AbortSignal): Promise<PilotHistory> {
  const query = new URLSearchParams(pilotWindow)
  const response = await fetch(`${baseUrl}api/history/dj1-no2?${query}`, { cache: 'no-store', signal })
  if (!response.ok) throw Error('History unavailable')
  const text = await response.text()
  if (text.length > maximumBytes) throw Error('History too large')
  return validatePilotHistory(JSON.parse(text), pilotWindow.from, pilotWindow.to)
}
