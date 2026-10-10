import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const context = { require: () => ({test: () => {}}) };
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../ops/checkly/served-data.check.js', import.meta.url), 'utf8'), context);
test('Checkly snapshot monitor rejects stale and malformed data at exact boundaries', () => {
const now = Date.parse('2026-10-10T13:00:00Z');
const row = {stationId:'RO0001A',pollutant:'PM10',source:'EEA',value:10,observedFrom:'2026-10-10T11:00:00Z',observedTo:'2026-10-10T12:00:00Z',reportedAt:'2026-10-10T12:00:00Z',ingestedAt:'2026-10-10T12:00:00Z'};
const doc = () => ({observations:[{...row}],importSummary:{attempted:1,imported:1,skipped:0,failed:0}});
let cases = 0;
for (const age of [0,3600000,7199999,7200000,19320000,-1]) {
 const d=doc();d.observations[0].ingestedAt=new Date(now-age).toISOString();
 d.observations[0].observedFrom='2026-10-10T05:00:00Z';d.observations[0].observedTo='2026-10-10T06:00:00Z';d.observations[0].reportedAt='2026-10-10T06:00:00Z';
 const r=context.assessSnapshot(d,now);
 assert.equal(r.ingestion_is_recent,age>=0&&age<7200000);assert.equal(r.ingestion_age_hours,age/3600000);cases++;
}
for (const age of [0,6*3600000-1,6*3600000,-1]) {
 const d=doc();const end=now-age;
 d.observations[0].observedTo=new Date(end).toISOString();d.observations[0].observedFrom=new Date(end-3600000).toISOString();d.observations[0].reportedAt=new Date(now).toISOString();d.observations[0].ingestedAt=new Date(now).toISOString();
 assert.equal(context.assessSnapshot(d,now).recent_readings,age>=0&&age<6*3600000?1:0);cases++;
}
for (const mutate of [d=>d.observations=[],d=>delete d.importSummary,d=>{d.importSummary.failed=1;d.importSummary.attempted=2;},d=>d.importSummary.imported=2,d=>d.observations[0].reportedAt='bad',d=>d.observations[0].ingestedAt='2026-10-10T12:00:00',d=>d.observations[0].value=Infinity,d=>d.observations[0].pollutant='bad',d=>{d.observations.push({...row});d.importSummary.imported=2;d.importSummary.attempted=2;}]) {
 const d=doc();mutate(d);assert.throws(()=>context.assessSnapshot(d,now));cases++;
}
assert.equal(cases, 19);
});
