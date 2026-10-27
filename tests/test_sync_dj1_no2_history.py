import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import psycopg
import pytest

from scripts import sync_dj1_no2_history as sync

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def database(local_database_dsn):
    dsn = local_database_dsn
    with psycopg.connect(dsn, autocommit=True) as admin:
        site = admin.execute(
            """INSERT INTO atlas.canonical_sites(name,location)
               VALUES ('DJ-1 test fixture',extensions.ST_SetSRID(extensions.ST_MakePoint(23.8062,44.3186),4326)::extensions.geography)
               RETURNING id"""
        ).fetchone()[0]
        sources = []
        for code, external in (
            ("EEA", sync.PILOT_SERIES),
            ("RNMCA", sync.RNMCA_STREAM),
        ):
            source = admin.execute(
                "INSERT INTO atlas.sources(code,name) VALUES (%s,%s) RETURNING id",
                (code, code),
            ).fetchone()[0]
            sources.append(source)
            admin.execute(
                """INSERT INTO atlas.source_streams(source_id,external_stream_id,format,source_url,rights_status,storage_allowed)
                   VALUES (%s,%s,'pilot-replay','urn:atlas:local-fixture','verified',true)""",
                (source, external),
            )
            admin.execute(
                """INSERT INTO atlas.source_devices(source_id,external_device_id,canonical_site_id,provider_location)
                   VALUES (%s,%s,%s,extensions.ST_SetSRID(extensions.ST_MakePoint(23.8062,44.3186),4326)::extensions.geography)""",
                (source, external, site),
            )
        with psycopg.connect(dsn, autocommit=True) as worker:
            worker.execute("SET ROLE atlas_pilot_ingestor")
            try:
                yield worker, admin, dsn
            finally:
                with admin.transaction():
                    admin.execute(
                        "DELETE FROM atlas.observations WHERE source_id = ANY(%s)",
                        (sources,),
                    )
                    admin.execute("DELETE FROM atlas.pilot_reports")
                    admin.execute(
                        "DELETE FROM atlas.source_devices WHERE source_id = ANY(%s)",
                        (sources,),
                    )
                    admin.execute(
                        "DELETE FROM atlas.source_streams WHERE source_id = ANY(%s)",
                        (sources,),
                    )
                    admin.execute(
                        "DELETE FROM atlas.sources WHERE id = ANY(%s)", (sources,)
                    )
                    admin.execute(
                        "DELETE FROM atlas.canonical_sites WHERE id = %s", (site,)
                    )


@pytest.fixture
def captured():
    return (
        json.loads((FIXTURES / "rnmca_dj1_no2.json").read_text())["dataElement"],
        json.loads((FIXTURES / "eea_dj1_no2.json").read_text())["eeaObservation"],
    )


def run(connection, raw=(), eea=()):
    return sync.sync_dj1_no2_history(
        connection,
        raw,
        eea,
        window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    )


def test_saves_selected_rnmca_and_full_report(database, captured):
    worker, admin, _ = database
    raw, eea = captured
    result = run(worker, [raw], [eea])
    row = admin.execute(
        "SELECT value,observed_from,observed_to,reported_at,quality_details,pilot_report_id FROM atlas.observations"
    ).fetchone()
    assert row[0] == Decimal(str(raw["dataElementFields"]["value"]))
    assert row[1] == datetime(2026, 10, 10, 18, tzinfo=UTC)
    assert row[2] == datetime(2026, 10, 10, 19, tzinfo=UTC)
    assert row[3] is None
    assert row[5] == result["reportId"]
    assert row[4]["normalizationRule"] == "rnmca-dj1-no2-hourly-epoch-utc-v1"
    report = admin.execute(
        "SELECT report FROM atlas.pilot_reports WHERE id = %s", (result["reportId"],)
    ).fetchone()[0]
    assert report["currentRnmcaRevisions"] == [raw]
    assert report["eeaObservations"][0]["rawObservation"] == eea
    assert (
        admin.execute(
            "SELECT extensions.ST_SRID(observation_location::extensions.geometry) FROM atlas.observations"
        ).fetchone()[0]
        == 4326
    )


def test_eea_fallback_uses_canonical_interval(database, captured):
    worker, admin, _ = database
    _, eea = captured
    admin.execute(
        "UPDATE atlas.source_devices SET provider_location = extensions.ST_SetSRID(extensions.ST_MakePoint(23.9,44.4),4326)::extensions.geography"
    )
    run(worker, eea=[eea])
    row = admin.execute(
        "SELECT value,observed_to,quality_details FROM atlas.observations"
    ).fetchone()
    assert row[0] == Decimal("48.81862")
    assert row[1] == datetime(2026, 10, 10, 19, tzinfo=UTC)
    location = admin.execute(
        "SELECT extensions.ST_X(observation_location::extensions.geometry),extensions.ST_Y(observation_location::extensions.geometry) FROM atlas.observations"
    ).fetchone()
    assert location == pytest.approx((eea["longitude"], eea["latitude"]))
    assert row[2]["sourceUrl"] == eea["sourceUrl"]
    assert (
        row[2]["normalizationRule"]
        == sync.reconcile_dj1_no2_window(
            [],
            [eea],
            window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
            window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
            ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
        )["normalizationRule"]
    )


def test_rejections_only_save_evidence(database, captured):
    worker, admin, _ = database
    raw, eea = captured
    raw["usable"] = False
    eea["validity"] = -1
    result = run(worker, [raw], [eea])
    assert result["observationCount"] == 0
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 0
    report = admin.execute("SELECT report FROM atlas.pilot_reports").fetchone()[0]
    assert report["currentRnmcaRevisions"][0]["usable"] is False
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "qa-rejected"


def test_replay_deduplicates_report_and_observations(database, captured):
    worker, admin, _ = database
    raw, eea = captured
    first = run(worker, [raw], [eea])
    assert run(worker, [raw], [eea]) == first
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 1
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 1


def test_withdrawal_survives_stale_and_empty_captures(database, captured):
    worker, admin, _ = database
    raw, _ = captured
    run(worker, [raw])
    withdrawal = deepcopy(raw)
    withdrawal["usable"] = False
    withdrawal["dataElementFields"]["version"] += 1
    run(worker, [withdrawal])
    run(worker, [raw])
    run(worker)
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 0
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 2


def test_absence_does_not_withdraw_previous_observation(database, captured):
    worker, admin, _ = database
    raw, _ = captured
    first = run(worker, [raw])
    assert run(worker) == first
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 1


def test_eea_invalidation_survives_stale_replay(database, captured):
    worker, admin, _ = database
    _, eea = captured
    run(worker, eea=[eea])
    invalidation = dict(eea, validity=-1, reportedAt="2026-10-11T12:00:00+01:00")
    run(worker, eea=[invalidation])
    run(worker, eea=[eea])
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 0
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 2


def test_referenced_report_cannot_be_deleted(database, captured):
    worker, admin, _ = database
    result = run(worker, [captured[0]])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(
            "DELETE FROM atlas.pilot_reports WHERE id = %s", (result["reportId"],)
        )
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 1


def test_failed_observation_write_rolls_back_report_and_replacement(
    database, captured, monkeypatch
):
    worker, admin, _ = database
    raw, _ = captured
    original = run(worker, [raw])
    changed = deepcopy(raw)
    changed["dataElementFields"]["version"] += 1
    changed["dataElementFields"]["value"] = 50
    insert = sync.insert_history_observations

    def fail(*args):
        insert(*args)
        if args[-1]:
            raise RuntimeError("Injected write failure.")

    monkeypatch.setattr(sync, "insert_history_observations", fail)
    with pytest.raises(RuntimeError, match="Injected write failure"):
        run(worker, [changed])
    assert admin.execute("SELECT id FROM atlas.pilot_reports").fetchall() == [
        (original["reportId"],)
    ]
    assert admin.execute("SELECT value FROM atlas.observations").fetchone()[
        0
    ] == Decimal(str(raw["dataElementFields"]["value"]))


def test_denies_unapproved_stream(database, captured):
    worker, admin, _ = database
    admin.execute(
        "UPDATE atlas.source_streams SET storage_allowed = false WHERE external_stream_id = %s",
        (sync.RNMCA_STREAM,),
    )
    with pytest.raises(ValueError, match="Both approved"):
        run(worker, [captured[0]])
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 0


def test_rejects_privileged_connection(database, captured):
    _, admin, _ = database
    with pytest.raises(ValueError, match="dedicated"):
        run(admin, [captured[0]])


def test_parallel_replay_is_serialized(database, captured):
    _, admin, dsn = database
    raw, eea = captured
    barrier = Barrier(2)

    def write():
        with psycopg.connect(dsn, autocommit=True) as worker:
            worker.execute("SET ROLE atlas_pilot_ingestor")
            barrier.wait(timeout=5)
            return run(worker, [raw], [eea])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0] == results[1]
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 1
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 1


def test_nonexistent_evidence_survives_empty_replay(database, captured):
    worker, admin, _ = database
    eea = dict(
        captured[1],
        observedFrom="2026-03-29T01:00:00+01:00",
        observedTo="2026-03-29T02:00:00+01:00",
    )
    options = dict(
        window_start=datetime(2026, 3, 29, 0, tzinfo=UTC),
        window_end=datetime(2026, 3, 29, 3, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    )
    first = sync.sync_dj1_no2_history(worker, [], [eea], **options)
    second = sync.sync_dj1_no2_history(worker, [], [], **options)
    assert second == first
    report = admin.execute(
        "SELECT report FROM atlas.pilot_reports WHERE id=%s", (second["reportId"],)
    ).fetchone()[0]
    assert report["excludedEeaObservations"][0]["temporalStatus"] == "nonexistent"


def test_legacy_writer_role_cannot_read_pilot_observations(database, captured):
    worker, _, dsn = database
    run(worker, eea=[captured[1]])
    with psycopg.connect(dsn, autocommit=True) as legacy:
        legacy.execute("SET ROLE atlas_ingestor")
        assert (
            legacy.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 0
        )


@pytest.mark.parametrize("value", [1e-30, 1e30, 48.81862])
def test_source_numeric_precision_is_preserved(database, captured, value):
    worker, admin, _ = database
    eea = dict(captured[1], value=value)
    run(worker, eea=[eea])
    assert admin.execute("SELECT value FROM atlas.observations").fetchone()[
        0
    ] == Decimal(str(value))


def test_overlapping_window_preserves_observations_outside_replacement(
    database, captured
):
    worker, admin, _ = database
    raw, _ = captured
    run(worker, [raw])
    shifted = deepcopy(raw)
    shifted["timestamp"] += 3_600_000
    shifted["dataElementFields"].update(minTimestamp=None, maxTimestamp=None)
    sync.sync_dj1_no2_history(
        worker,
        [shifted],
        [],
        window_start=datetime(2026, 10, 10, 19, tzinfo=UTC),
        window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    )
    rows = admin.execute(
        "SELECT observed_to FROM atlas.observations ORDER BY observed_to"
    ).fetchall()
    assert rows == [
        (datetime(2026, 10, 10, 19, tzinfo=UTC),),
        (datetime(2026, 10, 10, 20, tzinfo=UTC),),
    ]


def test_newer_rnmca_correction_replaces_value(database, captured):
    worker, admin, _ = database
    raw, eea = captured
    first = run(worker, [raw], [eea])
    corrected = deepcopy(raw)
    corrected["dataElementFields"]["version"] += 1
    corrected["dataElementFields"]["value"] = 50
    second = run(worker, [corrected])
    assert second["reportId"] != first["reportId"]
    assert admin.execute("SELECT value FROM atlas.observations").fetchall() == [
        (Decimal(50),)
    ]
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 2


def test_rejected_rnmca_can_use_retained_eligible_eea(database, captured):
    worker, admin, _ = database
    raw, eea = captured
    run(worker, [raw], [eea])
    withdrawn = deepcopy(raw)
    withdrawn["usable"] = False
    withdrawn["dataElementFields"]["version"] += 1
    run(worker, [withdrawn])
    row = admin.execute(
        "SELECT p.code,o.value FROM atlas.observations o JOIN atlas.sources p ON p.id=o.source_id"
    ).fetchone()
    assert row == ("EEA", Decimal(str(eea["value"])))


def test_malformed_new_revision_keeps_previous_committed_state(database, captured):
    worker, admin, _ = database
    raw, _ = captured
    original = run(worker, [raw])
    malformed = deepcopy(raw)
    malformed["dataElementFields"]["version"] += 1
    malformed["dataElementFields"]["qaqcStateId"] = -1
    with pytest.raises(ValueError):
        run(worker, [malformed])
    assert admin.execute("SELECT id FROM atlas.pilot_reports").fetchall() == [
        (original["reportId"],)
    ]
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone()[0] == 1


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_database_rejects_nonfinite_numeric(database, captured, value):
    worker, admin, _ = database
    run(worker, [captured[0]])
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("UPDATE atlas.observations SET value = %s::numeric", (value,))


def test_pilot_role_requires_report_reference(database, captured):
    worker, _, _ = database
    run(worker, [captured[0]])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute(
            """INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,
                 aggregation_type,observed_from,observed_to,value,unit)
               SELECT source_id,stream_id,device_id,parameter_code,aggregation_type,
                 observed_from+interval '1 hour',observed_to+interval '1 hour',value,unit
               FROM atlas.observations"""
        )


def test_pilot_role_cannot_delete_legacy_history(database, captured):
    worker, admin, _ = database
    run(worker, eea=[captured[1]])
    source = admin.execute("SELECT id FROM atlas.sources WHERE code='EEA'").fetchone()[
        0
    ]
    stream = admin.execute(
        "INSERT INTO atlas.source_streams(source_id,external_stream_id,format,rights_status,storage_allowed) VALUES (%s,'legacy','parquet','verified',true) RETURNING id",
        (source,),
    ).fetchone()[0]
    device = admin.execute(
        "INSERT INTO atlas.source_devices(source_id,external_device_id) VALUES (%s,'legacy') RETURNING id",
        (source,),
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO atlas.observations(source_id,stream_id,device_id,parameter_code,aggregation_type,observed_from,observed_to,value,unit) VALUES (%s,%s,%s,'NO2','hour','2026-10-10T18:00Z','2026-10-10T19:00Z',12,'ug.m-3')",
        (source, stream, device),
    )
    assert (
        worker.execute(
            "DELETE FROM atlas.observations WHERE stream_id=%s", (stream,)
        ).rowcount
        == 0
    )
    assert admin.execute(
        "SELECT value FROM atlas.observations WHERE stream_id=%s", (stream,)
    ).fetchone()[0] == Decimal(12)


def test_nonmatching_canonical_sites_fail_before_writes(database, captured):
    worker, admin, _ = database
    admin.execute(
        "UPDATE atlas.source_devices SET canonical_site_id=NULL WHERE external_device_id=%s",
        (sync.RNMCA_STREAM,),
    )
    with pytest.raises(ValueError, match="same canonical site"):
        run(worker, [captured[0]])
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 0


def test_earlier_replay_context_cannot_replace_later_state(database, captured):
    worker, admin, _ = database
    run(worker, [captured[0]])
    with pytest.raises(ValueError, match="must not move backwards"):
        sync.sync_dj1_no2_history(
            worker,
            [captured[0]],
            [],
            window_start=datetime(2026, 10, 10, 18, tzinfo=UTC),
            window_end=datetime(2026, 10, 10, 20, tzinfo=UTC),
            ingested_at=datetime(2026, 10, 12, 8, 59, tzinfo=UTC),
        )
    assert admin.execute("SELECT count(*) FROM atlas.pilot_reports").fetchone()[0] == 1
