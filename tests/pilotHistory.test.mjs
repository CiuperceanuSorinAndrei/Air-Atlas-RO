import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fetchPilotHistory, pilotHours, pilotWindow, validatePilotHistory } from '../src/pilotHistory.ts'

const response = () => JSON.parse(readFileSync(new URL('./fixtures/pilot_history_response.json', import.meta.url)))
const check = (document) => validatePilotHistory(document, pilotWindow.from, pilotWindow.to)

test('API response for the captured hour satisfies the UI contract', () => {
  const history = check(response())
  const hours = pilotHours(history)
  assert.deepEqual(hours.map((row) => [row.observedTo, row.observation?.value ?? null, row.observation?.source ?? null]), [
    ['2026-10-10T19:00:00+00:00', '48.81861538888885', 'RNMCA'],
    ['2026-10-10T20:00:00+00:00', null, null],
  ])
})

test('history without displayable sources is valid and entirely missing', () => {
  const document = response()
  document.missingIntervals.unshift({ observedFrom: document.observations[0].observedFrom, observedTo: document.observations[0].observedTo })
  document.observations = []
  document.sources = []
  assert.equal(pilotHours(check(document)).every((row) => row.observation === null), true)
})

const mutations = {
  'wrong schema': (d) => { d.schemaVersion = 2 },
  'other station': (d) => { d.stationCode = 'DJ-2' },
  'other unit': (d) => { d.unit = 'mg.m-3' },
  'known gap cause': (d) => { d.gapCause = 'maintenance' },
  'window differs from request': (d) => { d.windowEnd = '2026-10-10T21:00:00+00:00' },
  'naive window': (d) => { d.windowStart = '2026-10-10T18:00:00' },
  'numeric value': (d) => { d.observations[0].value = 48.8 },
  'non-decimal value': (d) => { d.observations[0].value = 'NaN' },
  'source without display entry': (d) => { d.sources.pop() },
  'unknown source': (d) => { d.sources[0].code = 'CAMS' },
  'duplicate source': (d) => { d.sources[1].code = 'EEA' },
  'blank attribution': (d) => { d.sources[0].attributionText = ' ' },
  'unsafe licence link': (d) => { d.sources[0].licenceUrl = 'javascript:alert(1)' },
  'half-hour interval': (d) => { d.observations[0].observedFrom = '2026-10-10T18:30:00+00:00' },
  'interval outside window': (d) => { d.missingIntervals[0] = { observedFrom: '2026-10-10T20:00:00+00:00', observedTo: '2026-10-10T21:00:00+00:00' } },
  'hour both observed and missing': (d) => { d.missingIntervals[0] = { observedFrom: d.observations[0].observedFrom, observedTo: d.observations[0].observedTo } },
  'silently dropped hour': (d) => { d.missingIntervals = [] },
  'naive ingestion time': (d) => { d.observations[0].ingestedAt = '2026-10-12T09:00:00' },
  'invalid report time': (d) => { d.observations[0].reportedAt = 'soon' },
  'non-text state': (d) => { d.observations[0].validationState = 1 },
  'missing arrays': (d) => { delete d.missingIntervals },
}
for (const [name, mutate] of Object.entries(mutations)) {
  test(`rejects ${name}`, () => {
    const document = response()
    mutate(document)
    assert.throws(() => check(document))
  })
}
test('rejects a window that is not on whole hours', () => {
  const document = response()
  document.windowStart = '2026-10-10T18:30:00+00:00'
  document.windowEnd = '2026-10-10T19:30:00+00:00'
  document.missingIntervals = []
  assert.throws(() => validatePilotHistory(document, '2026-10-10T18:30:00Z', '2026-10-10T19:30:00Z'))
})
test('rejects a source that is not text', () => {
  const document = response()
  document.observations[0].source = { toString: () => 'RNMCA' }
  assert.throws(() => check(document))
})
test('rejects non-objects', () => {
  for (const value of [null, [], 'text', 1]) assert.throws(() => check(value))
})

test('fetch uses the same-origin API with an encoded fixed window and fails closed', async (t) => {
  const calls = []
  const reply = (body, ok = true) => ({ ok, text: async () => body })
  let next = reply(JSON.stringify(response()))
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push([url, options.cache]); return next })
  const signal = new AbortController().signal
  const history = await fetchPilotHistory('/Air-Atlas-RO/', signal)
  assert.equal(history.observations.length, 1)
  assert.deepEqual(calls, [['/Air-Atlas-RO/api/history/dj1-no2?from=2026-10-10T18%3A00%3A00Z&to=2026-10-10T20%3A00%3A00Z', 'no-store']])
  for (const bad of [reply('{}'), reply('not json'), reply(JSON.stringify(response()), false), reply(' '.repeat(200_001))]) {
    next = bad
    await assert.rejects(fetchPilotHistory('/Air-Atlas-RO/', signal))
  }
})
