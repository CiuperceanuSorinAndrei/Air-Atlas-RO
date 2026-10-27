import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from scripts.dj1_no2_pilot import reconcile_dj1_no2_window
from scripts.pilot_report_store import save_report

MIGRATION = next(
    (Path(__file__).parents[1] / "supabase/migrations").glob(
        "*_dj1_no2_history_pilot.sql"
    )
)
REPORT_DDL = MIGRATION.read_text().split("ALTER TABLE atlas.observations", 1)[0]


@pytest.fixture
def database(local_database_dsn):
    dsn = local_database_dsn
    schema = "pilot_test_" + uuid4().hex
    definition = REPORT_DDL
    with psycopg.connect(dsn) as connection:
        try:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
            )
            connection.execute(
                definition.replace("atlas.pilot_reports", f"{schema}.pilot_reports")
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
            yield connection
        finally:
            connection.rollback()


def test_preserves_accepted_and_rejected_evidence(database):
    report = {
        "normalizationRule": "eea-dj1-no2-national-clock-bucharest-v1",
        "observations": [
            {"value": 48.81862, "observedTo": "2026-10-10T19:00:00+00:00"}
        ],
        "excludedEeaObservations": [
            {
                "temporalStatus": "qa-rejected",
                "observedFrom": None,
                "observedTo": None,
                "rawObservation": {"validity": -1, "value": None},
            }
        ],
        "currentRnmcaRevisions": [
            {"usable": False, "dataElementFields": {"version": 2}}
        ],
    }
    row = database.execute(
        "INSERT INTO pilot_reports(report) VALUES (%s) RETURNING id, saved_at, report",
        (Jsonb(report),),
    ).fetchone()
    assert row[0] > 0
    assert row[1].utcoffset() is not None
    assert row[2] == report
    assert (
        database.execute(
            "SELECT report FROM pilot_reports WHERE id = %s", (row[0],)
        ).fetchone()[0]
        == report
    )


@pytest.mark.parametrize("payload", [None, [], "text", 3, True])
def test_rejects_non_object_json(database, payload):
    with pytest.raises(psycopg.errors.CheckViolation):
        database.execute(
            "INSERT INTO pilot_reports(report) VALUES (%s)", (Jsonb(payload),)
        )


def test_rejects_sql_null(database):
    with pytest.raises(psycopg.errors.NotNullViolation):
        database.execute("INSERT INTO pilot_reports(report) VALUES (NULL)")


def test_enables_row_level_security(database):
    assert (
        database.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'pilot_reports'::regclass"
        ).fetchone()[0]
        is True
    )


@pytest.mark.parametrize("rejected", [False, True])
def test_round_trips_real_pilot_report(database, rejected):
    fixtures = Path("tests/fixtures")
    eea = json.loads((fixtures / "eea_dj1_no2.json").read_text())["eeaObservation"]
    raw = json.loads((fixtures / "rnmca_dj1_no2.json").read_text())["dataElement"]
    if rejected:
        eea["validity"] = -1
        raw["usable"] = False
    report = reconcile_dj1_no2_window(
        [raw],
        [eea],
        window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    )
    database.execute("INSERT INTO pilot_reports(report) VALUES (%s)", (Jsonb(report),))
    saved = database.execute("SELECT report FROM pilot_reports").fetchone()[0]
    assert saved == report
    if rejected:
        assert saved["observations"] == []
        assert saved["excludedEeaObservations"][0]["temporalStatus"] == "qa-rejected"
        assert saved["currentRnmcaRevisions"][0]["usable"] is False
    else:
        assert saved["observations"][0]["source"] == "RNMCA"
        assert saved["observations"][0]["observedTo"] == "2026-10-10T19:00:00+00:00"


def test_read_role_cannot_see_evidence_without_policy(database):
    role = "pilot_reader_" + uuid4().hex
    database.execute("INSERT INTO pilot_reports(report) VALUES ('{}')")
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    database.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    database.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(role)
        )
    )
    database.execute(
        sql.SQL("GRANT SELECT ON pilot_reports TO {}").format(sql.Identifier(role))
    )
    database.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    assert database.execute("SELECT count(*) FROM pilot_reports").fetchone()[0] == 0


def test_identical_replay_returns_original_id_and_timestamp(database):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    report = {"normalizationRule": "v1", "observations": [{"value": 12}]}
    first = save_report(database, report, schema=schema)
    timestamp = database.execute("SELECT saved_at FROM pilot_reports").fetchone()[0]
    second = save_report(database, report, schema=schema)
    assert first == second
    assert database.execute(
        "SELECT count(*), min(saved_at) FROM pilot_reports"
    ).fetchone() == (1, timestamp)


def test_dictionary_order_does_not_create_a_new_report(database):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    first = save_report(database, {"a": 1, "b": {"c": 2, "d": 3}}, schema=schema)
    second = save_report(database, {"b": {"d": 3, "c": 2}, "a": 1}, schema=schema)
    assert first == second


@pytest.mark.parametrize(
    "changed",
    [
        {"normalizationRule": "v2", "value": 12, "usable": True},
        {"normalizationRule": "v1", "value": 13, "usable": True},
        {"normalizationRule": "v1", "value": 12, "usable": False},
    ],
)
def test_changed_evidence_is_a_separate_report(database, changed):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    original = {"normalizationRule": "v1", "value": 12, "usable": True}
    first = save_report(database, original, schema=schema)
    second = save_report(database, changed, schema=schema)
    assert first != second
    assert database.execute(
        "SELECT report FROM pilot_reports ORDER BY id"
    ).fetchall() == [(original,), (changed,)]


def test_database_rejects_duplicate_even_without_helper(database):
    database.execute("INSERT INTO pilot_reports(report) VALUES ('{}')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        database.execute("INSERT INTO pilot_reports(report) VALUES ('{}')")


def test_report_storage_remains_in_callers_transaction(database):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    with pytest.raises(RuntimeError, match="Later write failed"):
        with database.transaction():
            save_report(database, {"value": 12}, schema=schema)
            raise RuntimeError("Later write failed")
    assert database.execute("SELECT count(*) FROM pilot_reports").fetchone()[0] == 0


def test_large_report_can_be_replayed(database):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    report = {"rawEvidence": [uuid4().hex for _ in range(3000)]}
    first = save_report(database, report, schema=schema)
    assert save_report(database, report, schema=schema) == first


def test_concurrent_replays_commit_one_report(local_database_dsn):
    dsn = local_database_dsn
    schema = "pilot_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            definition = REPORT_DDL
            admin.execute(
                definition.replace("atlas.pilot_reports", f"{schema}.pilot_reports")
            )
            barrier = Barrier(2)

            def write():
                with psycopg.connect(dsn) as connection:
                    connection.execute("SET LOCAL statement_timeout = '5s'")
                    barrier.wait(timeout=5)
                    return save_report(connection, {"value": 12}, schema=schema)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write) for _ in range(2)]
                ids = [future.result(timeout=10) for future in futures]
            assert ids[0] == ids[1]
            assert (
                admin.execute(
                    sql.SQL("SELECT count(*) FROM {}.pilot_reports").format(
                        sql.Identifier(schema)
                    )
                ).fetchone()[0]
                == 1
            )
        finally:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
