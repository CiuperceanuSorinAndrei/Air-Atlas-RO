CREATE ROLE atlas_ingestor NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA atlas, extensions TO atlas_ingestor;
GRANT SELECT ON atlas.sources, atlas.source_streams, atlas.source_devices, atlas.observations TO atlas_ingestor;
GRANT UPDATE (etag, last_synced_at) ON atlas.source_streams TO atlas_ingestor;
GRANT INSERT ON atlas.source_devices, atlas.observations TO atlas_ingestor;
GRANT DELETE ON atlas.observations TO atlas_ingestor;
GRANT USAGE ON SEQUENCE atlas.source_devices_id_seq, atlas.observations_id_seq TO atlas_ingestor;

CREATE POLICY eea_ingestion_sources ON atlas.sources FOR SELECT TO atlas_ingestor USING (code = 'EEA');
CREATE POLICY eea_ingestion_stream_read ON atlas.source_streams FOR SELECT TO atlas_ingestor
USING (source_id IN (SELECT id FROM atlas.sources WHERE code = 'EEA'));
CREATE POLICY eea_ingestion_stream_update ON atlas.source_streams FOR UPDATE TO atlas_ingestor
USING (source_id IN (SELECT id FROM atlas.sources WHERE code = 'EEA') AND storage_allowed AND rights_status = 'verified')
WITH CHECK (source_id IN (SELECT id FROM atlas.sources WHERE code = 'EEA') AND storage_allowed AND rights_status = 'verified');
CREATE POLICY eea_ingestion_device_read ON atlas.source_devices FOR SELECT TO atlas_ingestor
USING (source_id IN (SELECT id FROM atlas.sources WHERE code = 'EEA'));
CREATE POLICY eea_ingestion_device_insert ON atlas.source_devices FOR INSERT TO atlas_ingestor
WITH CHECK (EXISTS (SELECT 1 FROM atlas.source_streams s WHERE s.source_id = source_devices.source_id
  AND s.external_stream_id = source_devices.external_device_id AND s.storage_allowed AND s.rights_status = 'verified'));
CREATE POLICY eea_ingestion_observation_read ON atlas.observations FOR SELECT TO atlas_ingestor
USING (source_id IN (SELECT id FROM atlas.sources WHERE code = 'EEA'));
CREATE POLICY eea_ingestion_observation_insert ON atlas.observations FOR INSERT TO atlas_ingestor
WITH CHECK (EXISTS (SELECT 1 FROM atlas.source_streams s WHERE s.id = observations.stream_id
  AND s.source_id = observations.source_id AND s.storage_allowed AND s.rights_status = 'verified'));
CREATE POLICY eea_ingestion_observation_delete ON atlas.observations FOR DELETE TO atlas_ingestor
USING (EXISTS (SELECT 1 FROM atlas.source_streams s WHERE s.id = observations.stream_id
  AND s.source_id = observations.source_id AND s.storage_allowed AND s.rights_status = 'verified'));
