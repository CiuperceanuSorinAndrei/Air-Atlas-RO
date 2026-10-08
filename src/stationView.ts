import type { AirQualityObservation } from './airQualityObservation'

export type Locality = {
  id: number
  name: string
  county: string
  latitude: number
  longitude: number
  aliases: string
}
export function searchText(value: string): string {
  return value
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLocaleLowerCase('ro')
    .trim()
}
export function searchLocalities(
  localities: Locality[],
  query: string,
): Locality[] {
  const key = searchText(query)
  if (key.length < 2) return []
  return localities
    .filter((place) =>
      searchText(`${place.name} ${place.county} ${place.aliases}`).includes(
        key,
      ),
    )
    .sort(
      (a, b) =>
        Number(searchText(b.name) === key) -
          Number(searchText(a.name) === key) ||
        Number(searchText(b.name).startsWith(key)) -
          Number(searchText(a.name).startsWith(key)) ||
        a.name.localeCompare(b.name, 'ro') ||
        a.county.localeCompare(b.county, 'ro'),
    )
    .slice(0, 7)
}
export function distanceKm(
  origin: { latitude: number; longitude: number },
  destination: { latitude: number; longitude: number },
): number {
  const radians = Math.PI / 180
  const latitude = (destination.latitude - origin.latitude) * radians
  const longitude = (destination.longitude - origin.longitude) * radians
  const value =
    Math.sin(latitude / 2) ** 2 +
    Math.cos(origin.latitude * radians) *
      Math.cos(destination.latitude * radians) *
      Math.sin(longitude / 2) ** 2
  return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, value))))
}
export function measurementAge(
  reading: Pick<AirQualityObservation, 'observedFrom' | 'observedTo'>,
  now: number,
): string {
  const end = Date.parse(reading.observedTo)
  if (Date.parse(reading.observedFrom) > now) return 'Interval viitor'
  if (end > now) return 'Interval în curs'
  const hours = Math.floor((now - end) / 3_600_000)
  if (hours === 0) return 'În ultima oră'
  if (hours < 24) return `Acum ${hours} ${hours === 1 ? 'oră' : 'ore'}`
  const days = Math.floor(hours / 24)
  return `Acum ${days} ${days === 1 ? 'zi' : 'zile'}`
}
export function validateLocalities(value: unknown): Locality[] {
  if (!Array.isArray(value) || value.length > 50_000)
    throw Error('Invalid locality catalogue')
  const ids = new Set<number>()
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') throw Error('Invalid locality')
    const row = entry as Record<string, unknown>
    if (
      !Number.isSafeInteger(row.id) ||
      Number(row.id) <= 0 ||
      ids.has(Number(row.id))
    )
      throw Error('Invalid locality ID')
    if (
      typeof row.name !== 'string' ||
      !row.name.trim() ||
      row.name.length > 200 ||
      typeof row.county !== 'string' ||
      !row.county.trim() ||
      row.county.length > 200 ||
      typeof row.aliases !== 'string' ||
      row.aliases.length > 10_000
    )
      throw Error('Invalid locality names')
    if (
      typeof row.latitude !== 'number' ||
      !Number.isFinite(row.latitude) ||
      Math.abs(row.latitude) > 90 ||
      typeof row.longitude !== 'number' ||
      !Number.isFinite(row.longitude) ||
      Math.abs(row.longitude) > 180
    )
      throw Error('Invalid locality coordinates')
    ids.add(Number(row.id))
  }
  return value as Locality[]
}
