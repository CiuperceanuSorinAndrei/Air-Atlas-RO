import type { AirQualityObservation } from './airQualityObservation'

const recentWindowMilliseconds = 6 * 60 * 60 * 1000

type ObservationTimes = Pick<AirQualityObservation, 'observedFrom' | 'observedTo' | 'reportedAt'>

export function isRecentObservation(reading: ObservationTimes, now: number): boolean {
  const started = Date.parse(reading.observedFrom)
  const ended = Date.parse(reading.observedTo)
  const reported = Date.parse(reading.reportedAt)
  return started <= now && reported <= now && ended >= started && now - ended < recentWindowMilliseconds
}
