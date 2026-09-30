#!/usr/bin/env bash
# Disposable CI database only. Never point these commands at a live project.
set -euo pipefail
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres
psql -d postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN;
CREATE DATABASE atlas_audit TEMPLATE template0;
SQL
for migration in supabase/migrations/*.sql; do
  psql -d atlas_audit -v ON_ERROR_STOP=1 -f "$migration"
done
psql -d atlas_audit -v ON_ERROR_STOP=1 -f supabase/test_foundation.sql
