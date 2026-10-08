import test from 'node:test'
import assert from 'node:assert/strict'
import { parseAddresses, searchAddresses } from '../src/addressSearch.ts'
const building = {
  formatted: 'Strada Unirii 1, Craiova',
  lat: 44.32,
  lon: 23.8,
  country_code: 'ro',
  result_type: 'building',
  housenumber: '1',
  rank: { match_type: 'full_match', confidence_building_level: 0.95 },
}
test('geocoding preserves building precision and never promotes a street or locality to an address', () => {
  assert.match(parseAddresses({ results: [building] })[0].precision, /clădire/)
  assert.match(
    parseAddresses({ results: [{ ...building, result_type: 'street' }] })[0]
      .precision,
    /nu este confirmat/,
  )
  assert.match(
    parseAddresses({ results: [{ ...building, result_type: 'city' }] })[0]
      .precision,
    /nu este o adresă exactă/,
  )
  assert.match(
    parseAddresses({
      results: [
        {
          ...building,
          rank: { match_type: 'full_match', confidence_building_level: 0.2 },
        },
      ],
    })[0].precision,
    /aproximativă/,
  )
  assert.deepEqual(parseAddresses({ results: [] }), [])
})
test('geocoding rejects malformed, oversized, non-Romanian and invalid-coordinate results', () => {
  for (const value of [
    null,
    {},
    { results: Array(11).fill(building) },
    { results: [{ ...building, lat: NaN }] },
    { results: [{ ...building, lon: 181 }] },
    { results: [{ ...building, country_code: 'bg' }] },
    { results: [{ ...building, formatted: '' }] },
  ])
    assert.throws(() => parseAddresses(value))
})
test('address requests are explicit, country-filtered and propagate cancellation', async (t) => {
  const controller = new AbortController()
  let called
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    called = { url: new URL(url), options }
    return new Response(JSON.stringify({ results: [building] }))
  })
  const results = await searchAddresses(
    '  Strada Unirii 1, Craiova  ',
    'test-key',
    controller.signal,
  )
  assert.equal(called.url.searchParams.get('filter'), 'countrycode:ro')
  assert.equal(called.url.searchParams.get('text'), 'Strada Unirii 1, Craiova')
  assert.equal(called.options.signal, controller.signal)
  assert.equal(results[0].latitude, building.lat)
  await assert.rejects(searchAddresses('ab', 'test-key', controller.signal))
  await assert.rejects(searchAddresses('Craiova', '', controller.signal))
})
test('address reader rejects oversized and unsuccessful responses', async (t) => {
  t.mock.method(
    globalThis,
    'fetch',
    async () => new Response('x'.repeat(128001)),
  )
  await assert.rejects(
    searchAddresses('Craiova', 'test-key', new AbortController().signal),
  )
  globalThis.fetch.mock.mockImplementation(
    async () => new Response('Unavailable', { status: 429 }),
  )
  await assert.rejects(
    searchAddresses('Craiova', 'test-key', new AbortController().signal),
  )
})
