BEGIN;
DO $$
DECLARE a bigint; b bigint; stream_a bigint; stream_b bigint; device_a bigint;
BEGIN
  INSERT INTO atlas.sources(code,name) VALUES('audit-a','Audit A') RETURNING id INTO a;
  INSERT INTO atlas.sources(code,name) VALUES('audit-b','Audit B') RETURNING id INTO b;
  INSERT INTO atlas.source_streams(source_id,external_stream_id,format) VALUES(a,'s','parquet') RETURNING id INTO stream_a;
  INSERT INTO atlas.source_streams(source_id,external_stream_id,format) VALUES(b,'s','parquet') RETURNING id INTO stream_b;
  INSERT INTO atlas.source_devices(source_id,external_device_id) VALUES(a,'d') RETURNING id INTO device_a;
  INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,aggregation_type,observed_from,observed_to,value,unit)
    VALUES(a,stream_a,device_a,'NO2','hour','2026-01-01T00:00:00Z','2026-01-01T01:00:00Z',20,'ug.m-3');
  BEGIN
    INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,aggregation_type,observed_from,observed_to,value,unit)
      VALUES(a,stream_b,device_a,'NO2','hour','2026-01-02T00:00:00Z','2026-01-02T01:00:00Z',20,'ug.m-3');
    RAISE EXCEPTION 'Cross-source stream accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,aggregation_type,observed_from,observed_to,value,unit)
      VALUES(a,stream_a,device_a,'NO2','hour','2026-01-02T00:00:00Z','2026-01-02T01:00:00Z','NaN','ug.m-3');
    RAISE EXCEPTION 'NaN accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,aggregation_type,observed_from,observed_to,value,unit)
      VALUES(a,stream_a,device_a,'NO2','hour','2026-01-02T00:00:00Z','2026-01-02T00:00:00Z',20,'ug.m-3');
    RAISE EXCEPTION 'Empty interval accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE atlas.source_streams SET public_display_allowed=true WHERE id=stream_a;
    RAISE EXCEPTION 'Unverified public rights accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  UPDATE atlas.source_streams SET rights_status='verified', storage_allowed=true,
    public_display_allowed=true, licence_url='https://creativecommons.org/licenses/by/4.0/',
    attribution_text='Audit source' WHERE id=stream_a;
  IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='atlas' AND c.relkind='r' AND NOT c.relrowsecurity) THEN
    RAISE EXCEPTION 'RLS missing';
  END IF;
  IF has_schema_privilege('anon','atlas','USAGE') OR has_schema_privilege('authenticated','atlas','USAGE') OR has_schema_privilege('service_role','atlas','USAGE') THEN
    RAISE EXCEPTION 'Private schema access granted';
  END IF;
END $$;
SET LOCAL ROLE anon;
DO $$ BEGIN
  BEGIN PERFORM count(*) FROM atlas.observations; RAISE EXCEPTION 'Anonymous read allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;
SET LOCAL ROLE authenticated;
DO $$ BEGIN
  BEGIN PERFORM count(*) FROM atlas.observations; RAISE EXCEPTION 'Authenticated read allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;
ROLLBACK;
