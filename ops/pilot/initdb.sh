#!/usr/bin/env bash
set -euo pipefail
source="${ATLAS_INIT_DIR:-/atlas}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN;
CREATE DATABASE atlas TEMPLATE template0;
SQL
for migration in "$source"/migrations/*.sql; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname atlas -f "$migration"
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname atlas \
  -v worker_password="$ATLAS_WORKER_PASSWORD" -v api_password="$ATLAS_API_PASSWORD" -f "$source/seed.sql"
