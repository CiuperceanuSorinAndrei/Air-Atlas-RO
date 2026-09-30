import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { validateObservationDocument, acceptSnapshot, groupByStation } from '../src/airQualityObservation.ts'
const baseline = JSON.parse(readFileSync(new URL('../src/data/observations.json', import.meta.url)))
test('published hourly and daily data satisfy runtime contract', () => {
  assert.equal(validateObservationDocument(baseline), baseline)
  assert.ok(groupByStation(baseline.observations).size > 0)
})
for (const [key, value] of [['value',NaN], ['value',true], ['latitude',91], ['unit','mg.m-3'], ['verification',true], ['sourceUrl','javascript:alert(1)'], ['stationId','RO9999A'], ['observedTo','2030-01-01T00:00:00Z'], ['ingestedAt','2026-09-30T10:00:00']]) {
  test(`rejects invalid ${key}: ${String(value)}`, () => {
    const copy = structuredClone(baseline)
    copy.observations[0][key] = value
    assert.throws(() => validateObservationDocument(copy))
  })
}
test('rejects duplicate pairs and wrong summaries', () => {
  const copy = structuredClone(baseline)
  copy.observations.push(copy.observations[0])
  copy.importSummary.imported++
  copy.importSummary.attempted++
  assert.throws(() => validateObservationDocument(copy))
  copy.observations.pop()
  assert.throws(() => validateObservationDocument(copy))
})
test('stale deployment response cannot replace a newer import', () => {
  const old = structuredClone(baseline)
  old.observations.forEach(row => { row.ingestedAt = '2020-01-01T00:00:00Z' })
  assert.throws(() => acceptSnapshot(baseline, old))
  assert.equal(acceptSnapshot(baseline, baseline), baseline)
})

test('bounded HTTP reading rejects oversized and malformed responses', async () => {
  const { readSnapshot } = await import('../src/fetchSnapshot.ts')
  await assert.rejects(readSnapshot(new Response('x'.repeat(2_000_001))))
  await assert.rejects(readSnapshot(new Response('{}', {status:503})))
  await assert.rejects(readSnapshot(new Response('not json')))
  assert.equal((await readSnapshot(new Response(JSON.stringify(baseline)))).observations.length, baseline.observations.length)
})
test('conflicting physical station metadata is rejected', () => {
  const copy = structuredClone(baseline)
  const first = copy.observations.find((row, index) => copy.observations.some((other, j) => j > index && row.stationId === other.stationId))
  assert.ok(first)
  const same = copy.observations.find(row => row !== first && row.stationId === first.stationId)
  same.latitude += 1
  assert.throws(() => validateObservationDocument(copy))
})
