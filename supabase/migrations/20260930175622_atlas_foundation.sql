-- Reproducible private foundation, captured from the empty live schema.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
CREATE SCHEMA IF NOT EXISTS atlas;

CREATE TABLE IF NOT EXISTS atlas.sources (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  code text NOT NULL,
  name text NOT NULL,
  homepage_url text,
  CONSTRAINT sources_pkey PRIMARY KEY (id),
  CONSTRAINT sources_code_key UNIQUE (code)
);
ALTER TABLE atlas.sources ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS atlas.source_streams (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  source_id bigint NOT NULL,
  external_stream_id text NOT NULL,
  format text NOT NULL,
  source_url text,
  etag text,
  sync_cursor text,
  last_synced_at timestamp with time zone,
  rights_status text NOT NULL DEFAULT 'unverified'::text,
  storage_allowed boolean NOT NULL DEFAULT false,
  public_display_allowed boolean NOT NULL DEFAULT false,
  licence_url text,
  attribution_text text,
  CONSTRAINT source_streams_pkey PRIMARY KEY (id),
  CONSTRAINT source_streams_source_id_external_stream_id_key UNIQUE (source_id, external_stream_id),
  CONSTRAINT source_streams_source_id_id_key UNIQUE (source_id, id),
  CONSTRAINT source_streams_source_id_fkey FOREIGN KEY (source_id) REFERENCES atlas.sources(id)
);
ALTER TABLE atlas.source_streams ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS atlas.canonical_sites (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  name text NOT NULL,
  location extensions.geography(Point,4326) NOT NULL,
  station_type text,
  area_class text,
  CONSTRAINT canonical_sites_pkey PRIMARY KEY (id)
);
ALTER TABLE atlas.canonical_sites ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS atlas.source_devices (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  source_id bigint NOT NULL,
  external_device_id text NOT NULL,
  canonical_site_id bigint,
  provider_location extensions.geography(Point,4326),
  instrument_class text,
  authority_tier text,
  CONSTRAINT source_devices_pkey PRIMARY KEY (id),
  CONSTRAINT source_devices_source_id_external_device_id_key UNIQUE (source_id, external_device_id),
  CONSTRAINT source_devices_source_id_id_key UNIQUE (source_id, id),
  CONSTRAINT source_devices_source_id_fkey FOREIGN KEY (source_id) REFERENCES atlas.sources(id),
  CONSTRAINT source_devices_canonical_site_id_fkey FOREIGN KEY (canonical_site_id) REFERENCES atlas.canonical_sites(id)
);
ALTER TABLE atlas.source_devices ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS atlas.observations (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  source_id bigint NOT NULL,
  stream_id bigint NOT NULL,
  device_id bigint NOT NULL,
  parameter_code text NOT NULL,
  aggregation_type text NOT NULL,
  observed_from timestamp with time zone NOT NULL,
  observed_to timestamp with time zone NOT NULL,
  value numeric(38,18) NOT NULL,
  unit text NOT NULL,
  reported_at timestamp with time zone,
  ingested_at timestamp with time zone NOT NULL DEFAULT now(),
  validation_state text,
  source_validity text,
  source_verification text,
  source_record_id text,
  observation_location extensions.geography(Point,4326),
  quality_details jsonb,
  CONSTRAINT observations_check CHECK ((observed_to >= observed_from)),
  CONSTRAINT observations_pkey PRIMARY KEY (id),
  CONSTRAINT observations_stream_id_device_id_parameter_code_aggregation_key UNIQUE (stream_id, device_id, parameter_code, aggregation_type, observed_from, observed_to),
  CONSTRAINT observations_source_id_fkey FOREIGN KEY (source_id) REFERENCES atlas.sources(id),
  CONSTRAINT observations_source_id_stream_id_fkey FOREIGN KEY (source_id, stream_id) REFERENCES atlas.source_streams(source_id, id),
  CONSTRAINT observations_source_id_device_id_fkey FOREIGN KEY (source_id, device_id) REFERENCES atlas.source_devices(source_id, id)
);
ALTER TABLE atlas.observations ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS atlas.gridded_assets (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  source_id bigint NOT NULL,
  stream_id bigint NOT NULL,
  external_asset_id text NOT NULL,
  measurement_domain text NOT NULL,
  parameter_code text NOT NULL,
  unit text NOT NULL,
  valid_from timestamp with time zone NOT NULL,
  valid_to timestamp with time zone NOT NULL,
  issued_at timestamp with time zone,
  asset_uri text NOT NULL,
  footprint extensions.geometry(Geometry,4326),
  quality_details jsonb,
  ingested_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT gridded_assets_source_id_fkey FOREIGN KEY (source_id) REFERENCES atlas.sources(id),
  CONSTRAINT gridded_assets_source_id_stream_id_fkey FOREIGN KEY (source_id, stream_id) REFERENCES atlas.source_streams(source_id, id),
  CONSTRAINT gridded_assets_measurement_domain_check CHECK ((measurement_domain = ANY (ARRAY['surface_model'::text, 'atmospheric_column'::text]))),
  CONSTRAINT gridded_assets_check CHECK ((valid_to >= valid_from)),
  CONSTRAINT gridded_assets_pkey PRIMARY KEY (id),
  CONSTRAINT gridded_assets_stream_id_external_asset_id_key UNIQUE (stream_id, external_asset_id)
);
ALTER TABLE atlas.gridded_assets ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS canonical_sites_location_idx ON atlas.canonical_sites USING gist(location);
CREATE INDEX IF NOT EXISTS gridded_assets_footprint_idx ON atlas.gridded_assets USING gist(footprint);
CREATE INDEX IF NOT EXISTS observations_device_time_idx ON atlas.observations(device_id, observed_to DESC);
REVOKE ALL ON SCHEMA atlas FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA atlas FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA atlas FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA atlas REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA atlas REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated, service_role;
