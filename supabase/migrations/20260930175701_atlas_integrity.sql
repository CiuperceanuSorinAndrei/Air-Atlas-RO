-- Cover foreign-key lookups without deleting the empty foundation's useful indexes.
CREATE INDEX source_devices_canonical_site_idx ON atlas.source_devices(canonical_site_id);
CREATE INDEX observations_source_stream_idx ON atlas.observations(source_id, stream_id);
CREATE INDEX observations_source_device_idx ON atlas.observations(source_id, device_id);
CREATE INDEX gridded_assets_source_stream_idx ON atlas.gridded_assets(source_id, stream_id);
-- Numeric NaN compares greater than every finite value in PostgreSQL.
ALTER TABLE atlas.observations ADD CONSTRAINT observations_finite_value CHECK (value <> 'NaN'::numeric);
ALTER TABLE atlas.observations ADD CONSTRAINT observations_positive_interval CHECK (observed_to > observed_from);
ALTER TABLE atlas.gridded_assets ADD CONSTRAINT gridded_assets_positive_interval CHECK (valid_to > valid_from);
ALTER TABLE atlas.source_streams ADD CONSTRAINT source_streams_public_rights CHECK (
  NOT public_display_allowed OR
  (storage_allowed AND rights_status = 'verified' AND
   licence_url IS NOT NULL AND btrim(licence_url) <> '' AND
   attribution_text IS NOT NULL AND btrim(attribution_text) <> '')
);
