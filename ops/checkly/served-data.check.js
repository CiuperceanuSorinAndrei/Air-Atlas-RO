const { test, expect } = require('@playwright/test');

function assessSnapshot(document, now) {
  if (!document || !Array.isArray(document.observations) || !document.observations.length) {
    throw new Error('Snapshot must contain nonempty observations.');
  }
  const rows = document.observations;
  const summary = document.importSummary;
  if (!summary || !['attempted', 'imported', 'skipped', 'failed'].every(key => Number.isInteger(summary[key]) && summary[key] >= 0) ||
      summary.imported !== rows.length || summary.attempted !== summary.imported + summary.skipped + summary.failed) {
    throw new Error('Invalid import summary.');
  }
  if (summary.failed !== 0) throw new Error('Served snapshot records failed imports.');
  let latestIngestion = -Infinity;
  let latestMeasurement = -Infinity;
  let recentReadings = 0;
  const pairs = new Set();
  for (const row of rows) {
    if (!row || typeof row.stationId !== 'string' || !row.stationId ||
        !['PM2.5', 'PM10', 'NO2', 'SO2', 'O3', 'CO'].includes(row.pollutant) ||
        row.source !== 'EEA' || !Number.isFinite(row.value)) throw new Error('Invalid observation.');
    const timestamps = ['observedFrom', 'observedTo', 'reportedAt', 'ingestedAt'].map(key => {
      const value = row[key];
      if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) throw new Error('Timestamp requires UTC offset.');
      const time = Date.parse(value);
      if (!Number.isFinite(time)) throw new Error('Invalid timestamp.');
      return time;
    });
    const [start, end, reported, ingested] = timestamps;
    const duration = end - start;
    if (![3600000, 86400000].includes(duration) || reported > ingested || start > ingested) {
      throw new Error('Invalid observation interval.');
    }
    const pair = `${row.stationId}/${row.pollutant}`;
    if (pairs.has(pair)) throw new Error('Duplicate station/pollutant pair.');
    pairs.add(pair);
    latestIngestion = Math.max(latestIngestion, ingested);
    latestMeasurement = Math.max(latestMeasurement, end);
    if (start <= now && reported <= now && now - end >= 0 && now - end < 6 * 3600000) recentReadings++;
  }
  const age = now - latestIngestion;
  return {
    readings: rows.length,
    recent_readings: recentReadings,
    latest_measurement: new Date(latestMeasurement).toISOString(),
    latest_ingestion: new Date(latestIngestion).toISOString(),
    ingestion_age_hours: age / 3600000,
    ingestion_is_recent: age >= 0 && age < 2 * 3600000
  };
}

test('Atlas public snapshot is fresh', async ({ request }) => {
  const url = 'https://ciuperceanusorinandrei.github.io/Air-Atlas-RO/observations.json';
  const response = await request.get(`${url}?monitor=${Date.now()}`, {
    headers: { 'Cache-Control': 'no-cache' },
    timeout: 30000,
    maxRedirects: 0
  });
  expect(response.status(), 'Public snapshot HTTP status').toBe(200);
  expect(response.url().split('?')[0], 'Public snapshot URL').toBe(url);
  const payload = await response.body();
  expect(payload.length, 'Snapshot size limit').toBeLessThanOrEqual(8 * 1024 * 1024);
  const report = assessSnapshot(JSON.parse(payload.toString('utf8')), Date.now());
  console.log(JSON.stringify(report));
  expect(report.recent_readings, 'Measurements younger than six hours').toBeGreaterThan(0);
  expect(report.ingestion_is_recent, `Ingestion age: ${report.ingestion_age_hours.toFixed(2)} hours; limit: two hours`).toBe(true);
});
