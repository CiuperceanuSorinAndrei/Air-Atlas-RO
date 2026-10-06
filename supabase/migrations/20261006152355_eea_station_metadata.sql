ALTER TABLE atlas.source_devices
  ADD COLUMN provider_station_name text,
  ADD COLUMN metadata_synced_at timestamptz;

ALTER TABLE atlas.source_devices
  ADD CONSTRAINT source_devices_current_metadata_complete CHECK (
    (provider_station_name IS NULL AND metadata_synced_at IS NULL)
    OR (
      provider_station_name IS NOT NULL
      AND btrim(provider_station_name) <> ''
      AND metadata_synced_at IS NOT NULL
      AND isfinite(metadata_synced_at)
      AND provider_location IS NOT NULL
    )
  );

GRANT UPDATE (provider_location, provider_station_name, metadata_synced_at)
ON atlas.source_devices TO atlas_ingestor;

CREATE POLICY eea_ingestion_device_metadata_update
ON atlas.source_devices
FOR UPDATE TO atlas_ingestor
USING (
  EXISTS (
    SELECT 1
    FROM atlas.source_streams s
    JOIN atlas.sources p ON p.id = s.source_id
    WHERE p.code = 'EEA'
      AND s.source_id = source_devices.source_id
      AND s.external_stream_id = source_devices.external_device_id
      AND s.format = 'parquet'
      AND s.storage_allowed
      AND s.rights_status = 'verified'
  )
)
WITH CHECK (
  EXISTS (
    SELECT 1
    FROM atlas.source_streams s
    JOIN atlas.sources p ON p.id = s.source_id
    WHERE p.code = 'EEA'
      AND s.source_id = source_devices.source_id
      AND s.external_stream_id = source_devices.external_device_id
      AND s.format = 'parquet'
      AND s.storage_allowed
      AND s.rights_status = 'verified'
  )
);
