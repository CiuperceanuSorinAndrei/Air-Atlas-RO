CREATE ROLE atlas_pilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA atlas TO atlas_pilot_reader;
GRANT SELECT (id, code) ON atlas.sources TO atlas_pilot_reader;
GRANT SELECT (id, source_id, external_stream_id, format, rights_status, storage_allowed,
  public_display_allowed, licence_url, attribution_text) ON atlas.source_streams TO atlas_pilot_reader;
GRANT SELECT (source_id, stream_id, parameter_code, aggregation_type, observed_from, observed_to,
  value, unit, reported_at, ingested_at, validation_state, source_validity, source_verification,
  quality_details) ON atlas.observations TO atlas_pilot_reader;

CREATE POLICY pilot_reader_source ON atlas.sources FOR SELECT TO atlas_pilot_reader
USING (code IN ('EEA', 'RNMCA'));

CREATE POLICY pilot_reader_stream ON atlas.source_streams FOR SELECT TO atlas_pilot_reader
USING (format = 'pilot-replay' AND storage_allowed AND rights_status = 'verified'
  AND public_display_allowed
  AND EXISTS (SELECT 1 FROM atlas.sources p WHERE p.id = source_id
    AND ((p.code = 'EEA' AND external_stream_id = 'RO/SPO-RO0078A_00008_100')
      OR (p.code = 'RNMCA' AND external_stream_id = '27064/6404090401'))));

CREATE POLICY pilot_reader_observation ON atlas.observations FOR SELECT TO atlas_pilot_reader
USING (parameter_code = 'NO2' AND aggregation_type = 'hour' AND pilot_report_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM atlas.source_streams s
    WHERE s.id = observations.stream_id AND s.source_id = observations.source_id));
