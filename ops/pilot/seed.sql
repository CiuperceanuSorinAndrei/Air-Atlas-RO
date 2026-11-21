-- Local pilot configuration only. The rights flags below let the disposable
-- Compose database show the captured DJ-1/NO2 hour; they are not evidence of a
-- display licence and must never be copied to a deployed database.
WITH site AS (
  INSERT INTO atlas.canonical_sites(name, location)
  VALUES ('DJ-1 local pilot', extensions.ST_SetSRID(extensions.ST_MakePoint(23.8062, 44.3186), 4326)::extensions.geography)
  RETURNING id
), source AS (
  INSERT INTO atlas.sources(code, name) VALUES ('EEA', 'European Environment Agency'), ('RNMCA', 'RNMCA')
  RETURNING id, code
), stream AS (
  INSERT INTO atlas.source_streams(source_id, external_stream_id, format, source_url, rights_status,
    storage_allowed, public_display_allowed, licence_url, attribution_text)
  SELECT id,
    CASE code WHEN 'EEA' THEN 'RO/SPO-RO0078A_00008_100' ELSE '27064/6404090401' END,
    'pilot-replay', 'urn:atlas:local-pilot', 'verified', true, true,
    CASE code WHEN 'EEA' THEN 'https://creativecommons.org/licenses/by/4.0/' ELSE 'https://www.calitateaer.ro/' END,
    CASE code WHEN 'EEA' THEN 'EEA, captura locala de test' ELSE 'RNMCA calitateaer.ro, captura locala de test' END
  FROM source
  RETURNING source_id, external_stream_id
)
INSERT INTO atlas.source_devices(source_id, external_device_id, canonical_site_id, provider_location)
SELECT stream.source_id, stream.external_stream_id, site.id,
  extensions.ST_SetSRID(extensions.ST_MakePoint(23.8062, 44.3186), 4326)::extensions.geography
FROM stream, site;

CREATE ROLE atlas_pilot_worker LOGIN NOINHERIT PASSWORD :'worker_password';
GRANT atlas_pilot_ingestor TO atlas_pilot_worker;
CREATE ROLE atlas_history_api LOGIN NOINHERIT PASSWORD :'api_password';
GRANT atlas_pilot_reader TO atlas_history_api;
