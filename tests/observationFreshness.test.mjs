import assert from 'node:assert/strict'
import test from 'node:test'
import { isRecentObservation } from '../src/observationFreshness.ts'

const now = Date.parse('2026-09-25T06:58:00Z')
const craiova = {
  observedFrom: '2026-09-25T07:00:00+01:00',
  observedTo: '2026-09-25T08:00:00+01:00',
  reportedAt: '2026-09-25T07:22:34+01:00',
}

test('includes a reported current hour and excludes future or expired readings', () => {
  assert.equal(isRecentObservation(craiova, now), true)
  assert.equal(isRecentObservation({ ...craiova, observedFrom: '2026-09-25T08:00:00+01:00' }, now), false)
  assert.equal(isRecentObservation({ ...craiova, reportedAt: '2026-09-25T08:01:00+01:00' }, now), false)
  assert.equal(isRecentObservation(craiova, Date.parse('2026-09-25T13:00:01Z')), false)
})
