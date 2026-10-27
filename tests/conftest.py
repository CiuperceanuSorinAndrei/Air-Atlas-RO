import os
from pathlib import Path

import psycopg
import pytest


@pytest.fixture
def local_database_dsn():
    dsn = os.environ.get("ATLAS_TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("Disposable PostgreSQL DSN not configured")
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        host = connection.info.host
        local = host in ("127.0.0.1", "localhost", "/private/tmp") or Path(
            host
        ).is_relative_to("/private/tmp/atlas-postgres-runtime")
        if connection.info.dbname != "atlas_audit" or not local:
            pytest.fail("Tests require the disposable local atlas_audit database")
    return dsn
