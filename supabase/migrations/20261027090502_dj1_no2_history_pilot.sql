CREATE TABLE atlas.pilot_reports (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    saved_at timestamp with time zone NOT NULL DEFAULT now(),
    report jsonb NOT NULL,
    content_hash bytea GENERATED ALWAYS AS (sha256(jsonb_send(report))) STORED UNIQUE,
    CONSTRAINT pilot_reports_report_object CHECK (jsonb_typeof(report) = 'object')
);

ALTER TABLE atlas.pilot_reports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON atlas.pilot_reports FROM PUBLIC;

ALTER TABLE atlas.observations
    ADD COLUMN pilot_report_id bigint REFERENCES atlas.pilot_reports(id);
CREATE INDEX observations_pilot_report_idx ON atlas.observations(pilot_report_id);

CREATE ROLE atlas_pilot_ingestor NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA atlas, extensions TO atlas_pilot_ingestor;
GRANT SELECT ON atlas.sources, atlas.source_streams, atlas.source_devices TO atlas_pilot_ingestor;
GRANT SELECT, INSERT ON atlas.pilot_reports TO atlas_pilot_ingestor;
GRANT SELECT, INSERT, DELETE ON atlas.observations TO atlas_pilot_ingestor;
GRANT USAGE ON SEQUENCE atlas.pilot_reports_id_seq, atlas.observations_id_seq TO atlas_pilot_ingestor;

CREATE POLICY pilot_source_read ON atlas.sources FOR SELECT TO atlas_pilot_ingestor
USING (code IN ('EEA', 'RNMCA'));

CREATE POLICY pilot_stream_read ON atlas.source_streams FOR SELECT TO atlas_pilot_ingestor
USING (format = 'pilot-replay' AND storage_allowed AND rights_status = 'verified'
  AND EXISTS (SELECT 1 FROM atlas.sources p WHERE p.id = source_id
    AND ((p.code = 'EEA' AND external_stream_id = 'RO/SPO-RO0078A_00008_100')
      OR (p.code = 'RNMCA' AND external_stream_id = '27064/6404090401'))));

CREATE POLICY pilot_device_read ON atlas.source_devices FOR SELECT TO atlas_pilot_ingestor
USING (EXISTS (SELECT 1 FROM atlas.source_streams s
  WHERE s.source_id = source_devices.source_id
    AND s.external_stream_id = source_devices.external_device_id));

CREATE POLICY pilot_report_read ON atlas.pilot_reports FOR SELECT TO atlas_pilot_ingestor USING (true);
CREATE POLICY pilot_report_insert ON atlas.pilot_reports FOR INSERT TO atlas_pilot_ingestor WITH CHECK (true);

CREATE POLICY pilot_observation_read ON atlas.observations FOR SELECT TO atlas_pilot_ingestor
USING (EXISTS (SELECT 1 FROM atlas.source_streams s
  WHERE s.id = observations.stream_id AND s.source_id = observations.source_id));

CREATE POLICY pilot_observation_delete ON atlas.observations FOR DELETE TO atlas_pilot_ingestor
USING (EXISTS (SELECT 1 FROM atlas.source_streams s
  WHERE s.id = observations.stream_id AND s.source_id = observations.source_id));

CREATE POLICY pilot_observation_insert ON atlas.observations FOR INSERT TO atlas_pilot_ingestor
WITH CHECK (pilot_report_id IS NOT NULL AND parameter_code = 'NO2'
  AND aggregation_type = 'hour' AND unit = 'ug.m-3' AND EXISTS (
  SELECT 1 FROM atlas.source_streams s JOIN atlas.source_devices d
    ON d.source_id = s.source_id AND d.external_device_id = s.external_stream_id
  WHERE s.id = observations.stream_id AND s.source_id = observations.source_id
    AND d.id = observations.device_id
));

ALTER TABLE atlas.observations ALTER COLUMN value TYPE numeric;
ALTER TABLE atlas.observations DROP CONSTRAINT observations_finite_value;
ALTER TABLE atlas.observations ADD CONSTRAINT observations_finite_value
    CHECK (value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));

CREATE POLICY eea_history_nonpilot ON atlas.observations AS RESTRICTIVE
FOR ALL TO atlas_ingestor
USING (EXISTS (SELECT 1 FROM atlas.source_streams s
  WHERE s.id = observations.stream_id AND s.source_id = observations.source_id
    AND s.format = 'parquet'))
WITH CHECK (EXISTS (SELECT 1 FROM atlas.source_streams s
  WHERE s.id = observations.stream_id AND s.source_id = observations.source_id
    AND s.format = 'parquet'));
