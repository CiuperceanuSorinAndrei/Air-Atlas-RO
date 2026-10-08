import test from 'node:test'
import assert from 'node:assert/strict'
import {
  distanceKm,
  measurementAge,
  searchLocalities,
  validateLocalities,
} from '../src/stationView.ts'
const places = [
  {
    id: 1,
    name: 'Iași',
    county: 'Iași',
    latitude: 47.16,
    longitude: 27.58,
    aliases: 'Iasi,Jassy',
  },
  {
    id: 2,
    name: 'Iași',
    county: 'Brașov',
    latitude: 45.8,
    longitude: 25.1,
    aliases: 'Iasi',
  },
  {
    id: 3,
    name: 'București',
    county: 'București',
    latitude: 44.43,
    longitude: 26.1,
    aliases: 'Bucharest',
  },
]
test('locality search preserves same-name places and accepts accents, county and aliases', () => {
  assert.equal(searchLocalities(places, 'iasi').length, 2)
  assert.equal(searchLocalities(places, 'Iași Brașov')[0].id, 2)
  assert.equal(searchLocalities(places, 'Bucharest')[0].id, 3)
  assert.deepEqual(searchLocalities(places, 'i'), [])
})
test('distances are symmetric, zero at origin and plausible for Craiova to Bucharest', () => {
  const craiova = { latitude: 44.32, longitude: 23.8 }
  assert.equal(distanceKm(craiova, craiova), 0)
  const km = distanceKm(craiova, places[2])
  assert.ok(km > 180 && km < 190)
  assert.equal(km, distanceKm(places[2], craiova))
})
test('measurement age distinguishes future, running and elapsed intervals', () => {
  const reading = {
    observedFrom: '2026-10-08T10:00:00Z',
    observedTo: '2026-10-08T11:00:00Z',
  }
  const at = (text) => Date.parse(`2026-10-${text}Z`)
  assert.equal(measurementAge(reading, at('08T09:00:00')), 'Interval viitor')
  assert.equal(measurementAge(reading, at('08T10:30:00')), 'Interval în curs')
  assert.equal(measurementAge(reading, at('08T11:30:00')), 'În ultima oră')
  assert.equal(measurementAge(reading, at('08T12:30:00')), 'Acum 1 oră')
  assert.equal(measurementAge(reading, at('10T12:30:00')), 'Acum 2 zile')
})
test('catalogue rejects duplicate identity, malformed names and invalid coordinates', () => {
  assert.equal(validateLocalities(places), places)
  assert.throws(() => validateLocalities([places[0], places[0]]))
  for (const change of [
    { id: 0 },
    { latitude: NaN },
    { longitude: 181 },
    { county: '' },
    { aliases: null },
  ]) {
    assert.throws(() => validateLocalities([{ ...places[0], ...change }]))
  }
})
