export type AddressResult = {
  label: string
  latitude: number
  longitude: number
  precision: string
}

export function parseAddresses(value: unknown): AddressResult[] {
  if (
    !value ||
    typeof value !== 'object' ||
    !Array.isArray((value as { results?: unknown }).results)
  )
    throw Error('Invalid address response')
  const results = (value as { results: unknown[] }).results
  if (results.length > 10) throw Error('Too many address results')
  return results.map((entry) => {
    if (!entry || typeof entry !== 'object') throw Error('Invalid address')
    const row = entry as Record<string, unknown>
    if (
      typeof row.formatted !== 'string' ||
      !row.formatted.trim() ||
      row.formatted.length > 1000 ||
      row.country_code !== 'ro'
    )
      throw Error('Invalid address label or country')
    if (
      typeof row.lat !== 'number' ||
      !Number.isFinite(row.lat) ||
      Math.abs(row.lat) > 90 ||
      typeof row.lon !== 'number' ||
      !Number.isFinite(row.lon) ||
      Math.abs(row.lon) > 180
    )
      throw Error('Invalid address coordinates')
    const rank =
      row.rank && typeof row.rank === 'object'
        ? (row.rank as Record<string, unknown>)
        : {}
    const building =
      row.result_type === 'building' &&
      typeof row.housenumber === 'string' &&
      row.housenumber.trim() &&
      rank.match_type === 'full_match' &&
      typeof rank.confidence_building_level === 'number' &&
      rank.confidence_building_level >= 0.9 &&
      rank.confidence_building_level <= 1
    const precision = building
      ? 'Adresă identificată la nivel de clădire'
      : row.result_type === 'street'
        ? 'Stradă · numărul clădirii nu este confirmat'
        : ['city', 'locality', 'suburb', 'district', 'postcode'].includes(
              String(row.result_type),
            )
          ? 'Zonă sau localitate · nu este o adresă exactă'
          : 'Poziție aproximativă · verifică punctul pe hartă'
    return {
      label: row.formatted,
      latitude: row.lat,
      longitude: row.lon,
      precision,
    }
  })
}

export async function searchAddresses(
  query: string,
  key: string,
  signal: AbortSignal,
): Promise<AddressResult[]> {
  const text = query.trim()
  if (!key.trim() || text.length < 3 || text.length > 250)
    throw Error('Invalid address query')
  const url = new URL('https://api.geoapify.com/v1/geocode/search')
  url.search = new URLSearchParams({
    text,
    apiKey: key,
    filter: 'countrycode:ro',
    lang: 'ro',
    format: 'json',
    limit: '5',
  }).toString()
  const response = await fetch(url, { signal })
  if (
    !response.ok ||
    !response.body ||
    Number(response.headers.get('content-length')) > 128_000
  )
    throw Error('Address service unavailable')
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let size = 0,
    body = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > 128_000) throw Error('Address response too large')
      body += decoder.decode(value, { stream: true })
    }
    body += decoder.decode()
    return parseAddresses(JSON.parse(body))
  } finally {
    await reader.cancel()
  }
}
