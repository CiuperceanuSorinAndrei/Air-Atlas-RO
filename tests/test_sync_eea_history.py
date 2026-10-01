import os
import threading
import urllib.request
from datetime import datetime
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import import_eea as importer
from scripts import sync_eea_history as sync

URL = "https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/RO/SPO-RO0080A_00008_100.parquet"


def history_bytes(validity=1, value=20.0):
    row = {
        "Value": value,
        "AggType": "hour",
        "Start": datetime(2026, 3, 28, 10),
        "End": datetime(2026, 3, 28, 11),
        "ResultTime": datetime(2026, 3, 28, 11),
        "Pollutant": 8,
        "Samplingpoint": "RO/SPO-RO0080A_00008_100",
        "Unit": "ug.m-3",
        "Validity": validity,
        "Verification": 1,
    }
    buffer = BytesIO()
    pq.write_table(pa.table({key: [value] for key, value in row.items()}), buffer)
    return buffer.getvalue()


@pytest.fixture
def metadata(monkeypatch):
    monkeypatch.setattr(
        importer,
        "get_station_metadata",
        lambda *args: {
            "stationId": "RO0080A",
            "stationName": "DJ-3",
            "longitude": 23.7787,
            "latitude": 44.3268,
        },
    )


def test_conditional_304_avoids_body_decode_and_metadata(monkeypatch):
    calls = []

    def fetch(request, _limit):
        calls.append(request.get_header("If-none-match"))
        raise HTTPError(URL, 304, "unchanged", Message(), None)

    monkeypatch.setattr(importer, "read_http_response", fetch)
    monkeypatch.setattr(
        importer, "decode_parquet_table", lambda *args: pytest.fail("decoded")
    )
    monkeypatch.setattr(
        importer, "get_station_metadata", lambda *args: pytest.fail("metadata")
    )
    assert sync.fetch_history_version(URL, '"old"') == (None, '"old"')
    assert calls == ['"old"']
    with pytest.raises(ValueError, match="Unexpected 304"):
        sync.fetch_history_version(URL, None)


def test_changed_version_normalizes_quality(monkeypatch, metadata):
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(), '"new"')
    )
    rows, etag = sync.fetch_history_version(URL, '"old"')
    assert etag == '"new"'
    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["value"] == 20.0
    assert rows[0]["status"] == "validated"
    assert rows[0]["sourceUrl"] == URL


def test_revoked_version_returns_empty_history(monkeypatch):
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(-1), '"new"')
    )
    monkeypatch.setattr(
        importer, "get_station_metadata", lambda *args: pytest.fail("metadata")
    )
    assert sync.fetch_history_version(URL, '"old"') == ([], '"new"')


@pytest.mark.parametrize("etag", [None, "bare", '"bad\r\nheader"'])
def test_changed_version_requires_valid_etag(monkeypatch, etag):
    monkeypatch.setattr(importer, "read_http_response", lambda *args: (b"", etag))
    with pytest.raises(ValueError, match="ETag"):
        sync.fetch_history_version(URL, '"old"')


def test_transient_conditional_download_retries(monkeypatch, metadata):
    calls = []

    def fetch(request, _limit):
        calls.append(request.get_header("If-none-match"))
        if len(calls) == 1:
            raise URLError("timeout")
        return history_bytes(), '"new"'

    monkeypatch.setattr(importer, "read_http_response", fetch)
    monkeypatch.setattr(sync.time, "sleep", lambda *args: None)
    assert sync.fetch_history_version(URL, '"old"')[1] == '"new"'
    assert calls == ['"old"', '"old"']


def test_partial_response_is_rejected(monkeypatch):
    class Response(BytesIO):
        status = 206

    class Opener:
        @staticmethod
        def open(*_args, **_kwargs):
            return Response(b"partial")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="complete HTTP 200"):
        importer.read_http_response(Request(URL), 100)


@pytest.fixture
def database():
    dsn = os.environ.get("ATLAS_TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("Disposable PostgreSQL DSN not configured")
    with psycopg.connect(dsn, autocommit=True) as admin:
        if admin.info.dbname != "atlas_audit" or admin.info.host not in (
            "127.0.0.1",
            "localhost",
            "/private/tmp",
        ):
            pytest.fail("Tests require the disposable local atlas_audit database")
        with admin.transaction():
            source_record: tuple[int] | None = admin.execute(
                "INSERT INTO atlas.sources(code,name) VALUES ('EEA','EEA') RETURNING id"
            ).fetchone()
            assert source_record is not None
            source_id = source_record[0]
            stream_record: tuple[int] | None = admin.execute(
                """INSERT INTO atlas.source_streams(source_id, external_stream_id, format, source_url,
                   rights_status, storage_allowed) VALUES (%s,'RO/SPO-RO0080A_00008_100','parquet',%s,'verified',true)
                   RETURNING id""",
                (source_id, URL),
            ).fetchone()
            assert stream_record is not None
            stream_id = stream_record[0]
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("SET ROLE atlas_ingestor")
            try:
                yield connection, admin, stream_id, source_id, dsn
            finally:
                with admin.transaction():
                    admin.execute(
                        "DELETE FROM atlas.observations WHERE source_id = %s",
                        (source_id,),
                    )
                    admin.execute(
                        "DELETE FROM atlas.source_devices WHERE source_id = %s",
                        (source_id,),
                    )
                    admin.execute(
                        "DELETE FROM atlas.source_streams WHERE source_id = %s",
                        (source_id,),
                    )
                    admin.execute(
                        "DELETE FROM atlas.sources WHERE id = %s", (source_id,)
                    )


def stored_state(admin, stream_id):
    return (
        admin.execute(
            "SELECT etag, last_synced_at FROM atlas.source_streams WHERE id=%s",
            (stream_id,),
        ).fetchone(),
        admin.execute(
            "SELECT id, value, ingested_at FROM atlas.observations WHERE stream_id=%s ORDER BY id",
            (stream_id,),
        ).fetchall(),
    )


def test_database_replacement_304_correction_and_revocation(
    database, monkeypatch, metadata
):
    connection, admin, stream_id, source_id, dsn = database
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(), '"one"')
    )
    assert sync.sync_stream(connection, stream_id)["status"] == "replaced"
    initial = stored_state(admin, stream_id)
    assert initial[0][0] == '"one"'
    assert initial[1][0][1] == 20

    def unchanged(*_args):
        raise HTTPError(URL, 304, "unchanged", Message(), None)

    monkeypatch.setattr(importer, "read_http_response", unchanged)
    assert sync.sync_stream(connection, stream_id)["status"] == "unchanged"
    assert stored_state(admin, stream_id)[1] == initial[1]
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(value=21), '"two"')
    )
    sync.sync_stream(connection, stream_id)
    assert stored_state(admin, stream_id)[1][0][1] == 21
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(-1), '"three"')
    )
    sync.sync_stream(connection, stream_id)
    final = stored_state(admin, stream_id)
    assert final[0][0] == '"three"'
    assert final[1] == []


def test_database_rollback_after_delete_preserves_rows_and_etag(
    database, monkeypatch, metadata
):
    connection, admin, stream_id, source_id, dsn = database
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(), '"one"')
    )
    sync.sync_stream(connection, stream_id)
    before = stored_state(admin, stream_id)
    rows, _ = sync.fetch_history_version(URL, None)
    assert rows is not None
    rows[0]["observedTo"] = rows[0]["observedFrom"]
    monkeypatch.setattr(sync, "fetch_history_version", lambda *args: (rows, '"two"'))
    with pytest.raises(psycopg.errors.CheckViolation):
        sync.sync_stream(connection, stream_id)
    assert stored_state(admin, stream_id) == before


def test_database_storage_denial_precedes_download(database, monkeypatch):
    connection, admin, stream_id, source_id, dsn = database
    admin.execute(
        "UPDATE atlas.source_streams SET storage_allowed=false WHERE id=%s",
        (stream_id,),
    )
    monkeypatch.setattr(
        sync, "fetch_history_version", lambda *args: pytest.fail("downloaded")
    )
    with pytest.raises(ValueError, match="unavailable for ingestion"):
        sync.sync_stream(connection, stream_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute(
            "UPDATE atlas.source_streams SET storage_allowed=true WHERE id=%s",
            (stream_id,),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SELECT count(*) FROM atlas.canonical_sites")


def test_database_concurrent_writers_observe_latest_etag(
    database, monkeypatch, metadata
):
    connection, admin, stream_id, source_id, dsn = database
    ready = threading.Event()
    release = threading.Event()
    requested = []
    errors = []
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(), '"one"')
    )
    rows, _ = sync.fetch_history_version(URL, None)

    def fetch(_url, etag):
        requested.append(etag)
        if etag is None:
            ready.set()
            assert release.wait(3)
            return rows, '"one"'
        return None, etag

    monkeypatch.setattr(sync, "fetch_history_version", fetch)

    def worker():
        try:
            with psycopg.connect(dsn, autocommit=True) as other:
                other.execute("SET ROLE atlas_ingestor")
                sync.sync_stream(other, stream_id)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(3)
    second = threading.Thread(target=worker)
    second.start()
    release.set()
    thread.join(5)
    second.join(5)
    assert not thread.is_alive() and not second.is_alive()
    assert errors == []
    assert requested == [None, '"one"']
    assert len(stored_state(admin, stream_id)[1]) == 1


@pytest.mark.parametrize("failure", ["network", "malformed", "missing-etag"])
def test_database_download_failure_preserves_checkpoint(
    database, monkeypatch, metadata, failure
):
    connection, admin, stream_id, source_id, dsn = database
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (history_bytes(), '"one"')
    )
    sync.sync_stream(connection, stream_id)
    before = stored_state(admin, stream_id)

    def fetch(*_args):
        if failure == "network":
            raise URLError("unavailable")
        if failure == "malformed":
            return b"invalid", '"two"'
        return history_bytes(), None

    monkeypatch.setattr(importer, "read_http_response", fetch)
    monkeypatch.setattr(sync.time, "sleep", lambda *args: None)
    with pytest.raises((URLError, ValueError, pa.ArrowException)):
        sync.sync_stream(connection, stream_id)
    assert stored_state(admin, stream_id) == before


def test_database_writer_rejects_admin_and_nested_transaction(database):
    connection, admin, stream_id, source_id, dsn = database
    with pytest.raises(ValueError, match="dedicated"):
        sync.sync_stream(admin, stream_id)
    with connection.transaction(), pytest.raises(ValueError, match="idle autocommit"):
        sync.sync_stream(connection, stream_id)


def test_history_conflicting_duplicates_and_foreign_invalid_rows_fail(
    monkeypatch, metadata
):
    table = pq.read_table(BytesIO(history_bytes()))
    row = table.to_pylist()[0]
    conflict = {**row, "Value": 21.0}
    monkeypatch.setattr(
        importer, "read_http_response", lambda *args: (b"unused", '"new"')
    )
    monkeypatch.setattr(
        importer,
        "decode_parquet_table",
        lambda *args: pa.table({key: [row[key], conflict[key]] for key in row}),
    )
    with pytest.raises(ValueError, match="Conflicting"):
        sync.fetch_history_version(URL, None)
    foreign = {**row, "Validity": -1, "Samplingpoint": "RO/SPO-RO0009R_00008_100"}
    monkeypatch.setattr(
        importer,
        "decode_parquet_table",
        lambda *args: pa.table({key: [value] for key, value in foreign.items()}),
    )
    with pytest.raises(ValueError, match="sampling point"):
        sync.fetch_history_version(URL, None)


def test_azure_unquoted_etag_is_preserved(monkeypatch, metadata):
    etag = "0x8DF1F8277642AB4"
    calls = []

    def fetch(request, _limit):
        calls.append(request.get_header("If-none-match"))
        return history_bytes(), etag

    monkeypatch.setattr(importer, "read_http_response", fetch)
    assert sync.fetch_history_version(URL, etag)[1] == etag
    assert calls == [etag]
