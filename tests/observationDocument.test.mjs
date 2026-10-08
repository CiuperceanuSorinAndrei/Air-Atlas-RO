import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { validateObservationDocument, acceptSnapshot, groupByStation, latestMeasurement } from '../src/airQualityObservation.ts'
const baseline = JSON.parse(readFileSync(new URL('../src/data/observations.json', import.meta.url)))
test('published hourly and daily data satisfy runtime contract', () => {
  assert.equal(validateObservationDocument(baseline), baseline)
  assert.ok(groupByStation(baseline.observations).size > 0)
})
for (const [key, value] of [['value',NaN], ['value',true], ['latitude',91], ['unit','ppm'], ['verification',true], ['sourceUrl','javascript:alert(1)'], ['stationId','RO9999A'], ['observedTo','2030-01-01T00:00:00Z'], ['ingestedAt','2026-09-30T10:00:00']]) {
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

for (const [code, pollutant] of [['00001','SO2'], ['00005','PM10'], ['00007','O3'], ['00008','NO2'], ['00010','CO'], ['06001','PM2.5']]) {
  test(`preserves ${pollutant} in its source unit and rejects crossed units`, () => {
    const row = { ...baseline.observations[0], pollutant, unit: pollutant === 'CO' ? 'mg.m-3' : 'ug.m-3', value: pollutant === 'CO' ? 0.19521 : 13.108 }
    row.samplingPointId = `RO/SPO-${row.stationId}_${code}_100`
    row.sourceUrl = `https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-${row.stationId}_${code}_100.parquet`
    const document = { observations: [row], importSummary: { attempted: 1, imported: 1, skipped: 0, failed: 0 } }
    assert.equal(validateObservationDocument(document), document)
    assert.equal(document.observations[0].value, pollutant === 'CO' ? 0.19521 : 13.108)
    row.unit = pollutant === 'CO' ? 'ug.m-3' : 'mg.m-3'
    assert.throws(() => validateObservationDocument(document))
    row.unit = 'ppm'
    assert.throws(() => validateObservationDocument(document))
  })
}

test('latest measurement handles absent data and compares instants across time zones', () => {
  assert.equal(latestMeasurement(null), null)
  assert.equal(latestMeasurement({ ...baseline, observations: [] }), null)
  const observations = [
    { ...baseline.observations[0], observedTo: '2026-10-07T12:00:00+03:00' },
    { ...baseline.observations[0], observedTo: '2026-10-07T10:30:00+01:00' },
  ]
  assert.equal(latestMeasurement({ ...baseline, observations }), Date.parse('2026-10-07T09:30:00Z'))
})
