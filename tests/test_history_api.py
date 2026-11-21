# ruff: noqa: F811
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from scripts import history_api
from tests.test_sync_dj1_no2_history import captured, database, run  # noqa: F401

WINDOW = "from=2026-10-10T18:00:00Z&to=2026-10-10T20:00:00Z"
BASE = "/Air-Atlas-RO/"


@pytest.fixture
def site(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Atlas</title>")
    (dist / "assets/app.js").write_text("console.log('atlas')")
    (tmp_path / "secret.txt").write_text("outside")
    (dist / "link.txt").symlink_to(tmp_path / "secret.txt")
    return dist


def serve(dsn, dist):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), history_api.handler_for(dsn, dist, BASE)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def fetch(origin, path, method="GET"):
    request = urllib.request.Request(origin + path, method=method)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.fixture
def offline(site):
    server, origin = serve("host=127.0.0.1 port=1 connect_timeout=1", site)
    yield origin
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    "query,hours",
    [
        (WINDOW, 2),
        ("from=2026-10-10T20:00:00%2B02:00&to=2026-10-10T19:00:00%2B00:00", 1),
        ("to=2026-10-17T18:00:00Z&from=2026-10-10T18:00:00Z", 168),
    ],
)
def test_window_accepts_bounded_whole_utc_hours(query, hours):
    start, end = history_api.parse_window(query)
    assert start.utcoffset() == end.utcoffset() == UTC.utcoffset(None)
    assert (end - start).total_seconds() == hours * 3600


@pytest.mark.parametrize(
    "query",
    [
        "",
        "from=2026-10-10T18:00:00Z",
        "from=2026-10-10T18:00:00&to=2026-10-10T20:00:00Z",
        "from=2026-10-10T18:00:00+00:00&to=2026-10-10T20:00:00Z",
        "from=2026-10-10T18:30:00Z&to=2026-10-10T20:00:00Z",
        "from=2026-10-10T18:00:01Z&to=2026-10-10T20:00:00Z",
        "from=2026-10-10T20:00:00Z&to=2026-10-10T20:00:00Z",
        "from=2026-10-10T20:00:00Z&to=2026-10-10T18:00:00Z",
        "from=2026-10-10T18:00:00Z&to=2026-10-17T19:00:00Z",
        WINDOW + "&station=other",
        WINDOW + "&to=2026-10-10T21:00:00Z",
        "from=yesterday&to=today",
        "from&to",
    ],
)
def test_window_rejects_everything_else(query):
    with pytest.raises(history_api.BadRequest):
        history_api.parse_window(query)


def test_static_site_shares_the_origin_without_leaving_dist(offline):
    status, headers, body = fetch(offline, BASE)
    assert status == 200 and b"<title>Atlas</title>" in body
    assert headers["Content-Type"] == "text/html"
    assert headers["X-Content-Type-Options"] == "nosniff"
    status, headers, body = fetch(offline, BASE + "assets/app.js")
    assert status == 200 and body == b"console.log('atlas')"
    assert fetch(offline, "/")[:2][0] == 302
    assert fetch(offline, "/")[1]["Location"] == BASE
    assert fetch(offline, BASE, "HEAD") == (200, fetch(offline, BASE)[1], b"")
    for path in (
        "/index.html",
        "/secret.txt",
        BASE + "../secret.txt",
        BASE + "%2e%2e/secret.txt",
        BASE + "..%2fsecret.txt",
        BASE + "link.txt",
        BASE + "assets",
        BASE + "assets/",
        BASE + "missing.js",
        BASE + "api/",
        BASE + "api/history/other",
        BASE + "%00",
    ):
        status, _, body = fetch(offline, path)
        assert status == 404, path
        assert b"outside" not in body
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        status, headers, body = fetch(offline, BASE + "api/health", method)
        assert status == 405 and headers["Allow"] == "GET, HEAD"


def test_database_failures_and_bad_input_are_generic(offline):
    for path in ("api/health", "api/history/dj1-no2?" + WINDOW):
        status, headers, body = fetch(offline, BASE + path)
        assert status == 503
        assert json.loads(body) == {"error": "Service Unavailable"}
        assert headers["Cache-Control"] == "no-store"
    status, _, body = fetch(offline, BASE + "api/history/dj1-no2?from=a&to=b")
    assert status == 400 and json.loads(body) == {"error": "Bad Request"}


def allow_display(admin, codes=("EEA", "RNMCA")):
    admin.execute(
        """UPDATE atlas.source_streams s SET public_display_allowed = true,
             licence_url = 'https://example.test/licence',
             attribution_text = 'Fixture ' || p.code
           FROM atlas.sources p
           WHERE p.id = s.source_id AND s.format = 'pilot-replay' AND p.code = ANY(%s)""",
        (list(codes),),
    )


def test_captured_observation_agrees_from_worker_to_api(database, captured, site):
    worker, admin, dsn = database
    raw, eea = captured
    run(worker, [raw], [eea])
    server, origin = serve(dsn, site)
    try:
        url = BASE + "api/history/dj1-no2?" + WINDOW
        status, headers, body = fetch(origin, url)
        hidden = json.loads(body)
        assert status == 200
        assert hidden["observations"] == [] and hidden["sources"] == []
        assert len(hidden["missingIntervals"]) == 2
        allow_display(admin, ["EEA"])
        partial = json.loads(fetch(origin, url)[2])
        assert partial["observations"] == []
        assert [row["code"] for row in partial["sources"]] == ["EEA"]
        allow_display(admin)
        status, headers, body = fetch(origin, url)
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert headers["Cache-Control"] == "no-store"
        document = json.loads(body)
        assert document == json.loads(
            Path("tests/fixtures/pilot_history_response.json").read_text()
        )
        stored = admin.execute(
            """SELECT value::text, validation_state, source_validity,
                 source_verification, quality_details->>'normalizationRule',
                 quality_details->>'temporalStatus' FROM atlas.observations"""
        ).fetchone()
        assert document.pop("observations") == [
            {
                "observedFrom": "2026-10-10T18:00:00+00:00",
                "observedTo": "2026-10-10T19:00:00+00:00",
                "value": str(raw["dataElementFields"]["value"]),
                "source": "RNMCA",
                "reportedAt": None,
                "ingestedAt": "2026-10-12T09:00:00+00:00",
                "validationState": stored[1],
                "sourceValidity": stored[2],
                "sourceVerification": stored[3],
                "normalizationRule": "rnmca-dj1-no2-hourly-epoch-utc-v1",
                "temporalStatus": stored[5],
            }
        ]
        assert stored[0] == str(raw["dataElementFields"]["value"])
        assert stored[2:5] == (
            str(raw["dataElementFields"]["qaqcStatusId"]),
            str(raw["dataElementFields"]["qaqcStateId"]),
            "rnmca-dj1-no2-hourly-epoch-utc-v1",
        )
        assert document == {
            "schemaVersion": 1,
            "stationCode": "DJ-1",
            "pollutant": "NO2",
            "unit": "ug.m-3",
            "aggregationType": "hour",
            "windowStart": "2026-10-10T18:00:00+00:00",
            "windowEnd": "2026-10-10T20:00:00+00:00",
            "sources": [
                {
                    "code": code,
                    "attributionText": f"Fixture {code}",
                    "licenceUrl": "https://example.test/licence",
                }
                for code in ("EEA", "RNMCA")
            ],
            "missingIntervals": [
                {
                    "observedFrom": "2026-10-10T19:00:00+00:00",
                    "observedTo": "2026-10-10T20:00:00+00:00",
                }
            ],
            "gapCause": "unknown",
        }
        assert str(raw["dataElementFields"]["value"]) == "48.81861538888885"
        earlier = BASE + (
            "api/history/dj1-no2?from=2026-10-10T16:00:00Z&to=2026-10-10T18:00:00Z"
        )
        before = json.loads(fetch(origin, earlier)[2])
        assert before["observations"] == [] and len(before["missingIntervals"]) == 2
        assert fetch(origin, BASE + "api/health")[0] == 200
        admin.execute("ALTER ROLE atlas_pilot_reader BYPASSRLS")
        try:
            assert fetch(origin, url)[0] == 503
            assert fetch(origin, BASE + "api/health")[0] == 503
        finally:
            admin.execute("ALTER ROLE atlas_pilot_reader NOBYPASSRLS")
        admin.execute(
            """INSERT INTO atlas.observations(source_id, stream_id, device_id,
                 parameter_code, aggregation_type, observed_from, observed_to, value,
                 unit, pilot_report_id)
               SELECT s.source_id, s.id, d.id, 'NO2', 'hour', o.observed_from,
                 o.observed_to, 1, 'ug.m-3', o.pilot_report_id
               FROM atlas.observations o, atlas.source_streams s
               JOIN atlas.sources p ON p.id = s.source_id
               JOIN atlas.source_devices d ON d.source_id = s.source_id
               WHERE p.code = 'EEA' AND s.format = 'pilot-replay'"""
        )
        status, _, body = fetch(origin, url)
        assert status == 500 and json.loads(body) == {"error": "Internal Server Error"}
    finally:
        server.shutdown()
        server.server_close()


def test_reader_role_sees_only_displayable_pilot_columns(database, captured):
    worker, admin, dsn = database
    raw, eea = captured
    run(worker, [raw], [eea])
    allow_display(admin)
    admin.execute(
        """INSERT INTO atlas.observations(source_id, stream_id, device_id,
             parameter_code, aggregation_type, observed_from, observed_to, value, unit)
           SELECT source_id, stream_id, device_id, parameter_code, aggregation_type,
             observed_from - interval '5 hours', observed_to - interval '5 hours',
             value, unit FROM atlas.observations"""
    )
    with psycopg.connect(dsn, autocommit=True) as reader:
        reader.execute("SET ROLE atlas_pilot_reader")
        assert reader.execute("SELECT count(*) FROM atlas.observations").fetchone() == (
            1,
        )
        assert reader.execute(
            "SELECT count(*) FROM atlas.source_streams"
        ).fetchone() == (2,)
        for statement in (
            "SELECT report FROM atlas.pilot_reports",
            "SELECT count(*) FROM atlas.pilot_reports",
            "SELECT count(*) FROM atlas.source_devices",
            "SELECT count(*) FROM atlas.canonical_sites",
            "SELECT count(*) FROM atlas.gridded_assets",
            "SELECT device_id FROM atlas.observations",
            "SELECT pilot_report_id FROM atlas.observations",
            "SELECT observation_location FROM atlas.observations",
            "SELECT source_record_id FROM atlas.observations",
            "SELECT source_url FROM atlas.source_streams",
            "SELECT name FROM atlas.sources",
            "UPDATE atlas.observations SET value = 0",
            "DELETE FROM atlas.observations",
            "INSERT INTO atlas.sources(code, name) VALUES ('X', 'X')",
            "UPDATE atlas.source_streams SET public_display_allowed = true",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement)
        admin.execute(
            "UPDATE atlas.source_streams SET format = 'parquet' WHERE format = 'pilot-replay'"
        )
        assert reader.execute("SELECT count(*) FROM atlas.observations").fetchone() == (
            0,
        )
        admin.execute(
            "UPDATE atlas.source_streams SET format = 'pilot-replay' WHERE format = 'parquet'"
        )


def test_login_without_reader_membership_is_unavailable(local_database_dsn, site):
    login = "atlas_test_login_" + uuid4().hex
    with psycopg.connect(local_database_dsn, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {login} LOGIN")
        try:
            dsn = f"{local_database_dsn} user={login}"
            server, origin = serve(dsn, site)
            try:
                for path in ("api/health", "api/history/dj1-no2?" + WINDOW):
                    assert fetch(origin, BASE + path)[0] == 503
            finally:
                server.shutdown()
                server.server_close()
        finally:
            admin.execute(f"DROP ROLE {login}")


def test_replay_cli_stores_the_captured_hour(database, capsys, monkeypatch):
    _, admin, dsn = database
    from scripts import sync_dj1_no2_history as sync

    monkeypatch.setenv("ATLAS_PILOT_INGESTION_DSN", dsn)
    monkeypatch.setattr(
        "sys.argv",
        [
            "sync_dj1_no2_history",
            "--rnmca",
            "tests/fixtures/rnmca_dj1_no2.json",
            "--eea",
            "tests/fixtures/eea_dj1_no2.json",
            "--window-start",
            "2026-10-10T18:00:00+00:00",
            "--window-end",
            "2026-10-10T20:00:00+00:00",
            "--ingested-at",
            "2026-10-12T09:00:00+00:00",
        ],
    )
    sync.main()
    assert json.loads(capsys.readouterr().out)["observationCount"] == 1
    assert admin.execute("SELECT observed_to FROM atlas.observations").fetchone() == (
        datetime(2026, 10, 10, 19, tzinfo=UTC),
    )
    sync.main()
    assert admin.execute("SELECT count(*) FROM atlas.observations").fetchone() == (1,)
    monkeypatch.delenv("ATLAS_PILOT_INGESTION_DSN")
    with pytest.raises(SystemExit):
        sync.main()
