import argparse
import json
import mimetypes
import os
from datetime import UTC, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

from scripts.import_eea import parse_timestamp


class BadRequest(Exception):
    pass


class Unavailable(Exception):
    pass


HOUR = timedelta(hours=1)
MAX_HOURS = 168
READER = "atlas_pilot_reader"


def parse_window(query):
    try:
        fields = parse_qs(
            query, keep_blank_values=True, strict_parsing=True, max_num_fields=2
        )
        if fields.keys() != {"from", "to"} or any(
            len(values) != 1 for values in fields.values()
        ):
            raise ValueError
        start, end = (
            parse_timestamp(fields[key][0]).astimezone(UTC) for key in ("from", "to")
        )
    except (ValueError, TypeError, OverflowError) as error:
        raise BadRequest from error
    if (
        any(
            moment.minute or moment.second or moment.microsecond
            for moment in (start, end)
        )
        or not HOUR <= end - start <= MAX_HOURS * HOUR
    ):
        raise BadRequest
    return start, end


def connect(dsn):
    connection = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    try:
        connection.execute(f"SET ROLE {READER}")
        role = connection.execute(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if role != (READER, False, False):
            raise Unavailable
    except BaseException:
        connection.close()
        raise
    return connection


def read_history(dsn, start, end):
    with connect(dsn) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        sources = connection.execute(
            """SELECT p.code, s.attribution_text, s.licence_url
               FROM atlas.source_streams s JOIN atlas.sources p ON p.id = s.source_id
               ORDER BY p.code"""
        ).fetchall()
        rows = connection.execute(
            """SELECT p.code, o.observed_from, o.observed_to, o.value, o.unit,
                      o.reported_at, o.ingested_at, o.validation_state,
                      o.source_validity, o.source_verification,
                      o.quality_details->>'normalizationRule',
                      o.quality_details->>'temporalStatus'
               FROM atlas.observations o JOIN atlas.sources p ON p.id = o.source_id
               WHERE o.observed_to > %s AND o.observed_to <= %s
               ORDER BY o.observed_to LIMIT %s""",
            (start, end, MAX_HOURS + 1),
        ).fetchall()
    observations = {}
    for row in rows:
        begin, finish = row[1].astimezone(UTC), row[2].astimezone(UTC)
        if (
            finish - begin != HOUR
            or finish.minute
            or finish.second
            or finish.microsecond
            or finish in observations
            or row[4] != "ug.m-3"
        ):
            raise RuntimeError("Stored pilot history is not one reading per UTC hour.")
        observations[finish] = {
            "observedFrom": begin.isoformat(),
            "observedTo": finish.isoformat(),
            "value": str(row[3]),
            "source": row[0],
            "reportedAt": row[5].astimezone(UTC).isoformat() if row[5] else None,
            "ingestedAt": row[6].astimezone(UTC).isoformat(),
            "validationState": row[7],
            "sourceValidity": row[8],
            "sourceVerification": row[9],
            "normalizationRule": row[10],
            "temporalStatus": row[11],
        }
    hours = [start + HOUR * step for step in range(1, (end - start) // HOUR + 1)]
    return {
        "schemaVersion": 1,
        "stationCode": "DJ-1",
        "pollutant": "NO2",
        "unit": "ug.m-3",
        "aggregationType": "hour",
        "windowStart": start.isoformat(),
        "windowEnd": end.isoformat(),
        "sources": [
            {"code": code, "attributionText": text, "licenceUrl": url}
            for code, text, url in sources
        ],
        "observations": [observations[hour] for hour in hours if hour in observations],
        "missingIntervals": [
            {"observedFrom": (hour - HOUR).isoformat(), "observedTo": hour.isoformat()}
            for hour in hours
            if hour not in observations
        ],
        "gapCause": "unknown",
    }


def respond(dsn, dist, base, target):
    parts = urlsplit(target)
    if parts.path == "/" and base != "/":
        return HTTPStatus.FOUND, "text/plain", b"", {"Location": base}
    if not parts.path.startswith(base):
        raise FileNotFoundError
    route = parts.path[len(base) :]
    if route == "api/health":
        connect(dsn).close()
        return HTTPStatus.OK, "application/json", b'{"status":"ok"}', {}
    if route == "api/history/dj1-no2":
        document = read_history(dsn, *parse_window(parts.query))
        return HTTPStatus.OK, "application/json", json.dumps(document).encode(), {}
    if route.startswith("api/") or "\x00" in unquote(route):
        raise FileNotFoundError
    try:
        path = (dist / (unquote(route) or "index.html")).resolve()
        found = path.is_relative_to(dist) and path.is_file()
    except (ValueError, OSError) as error:
        raise FileNotFoundError from error
    if not found:
        raise FileNotFoundError
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return HTTPStatus.OK, kind, path.read_bytes(), {}


def handler_for(dsn, dist, base):
    dist = dist.resolve()

    class Handler(BaseHTTPRequestHandler):
        timeout = 10
        server_version = "AtlasHistory"
        sys_version = ""

        def reply(self, status, kind, body, headers):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            try:
                self.reply(*respond(dsn, dist, base, self.path))
                return
            except BadRequest:
                status = HTTPStatus.BAD_REQUEST
            except FileNotFoundError:
                status = HTTPStatus.NOT_FOUND
            except (psycopg.Error, Unavailable):
                status = HTTPStatus.SERVICE_UNAVAILABLE
            except Exception:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = json.dumps({"error": status.phrase}).encode()
            self.reply(status, "application/json", body, {})

        do_HEAD = do_GET

        def refuse(self):
            body = json.dumps({"error": HTTPStatus.METHOD_NOT_ALLOWED.phrase}).encode()
            self.reply(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "application/json",
                body,
                {"Allow": "GET, HEAD"},
            )

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = refuse

    return Handler


def main():
    parser = argparse.ArgumentParser(
        description="Local read-only DJ-1/NO2 history pilot; not hardened for public exposure."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--base", default="/Air-Atlas-RO/")
    args = parser.parse_args()
    dsn = os.environ.get("ATLAS_HISTORY_DSN")
    if not dsn:
        parser.error("ATLAS_HISTORY_DSN is required.")
    if not args.base.startswith("/") or not args.base.endswith("/"):
        parser.error("--base must start and end with a slash.")
    server = ThreadingHTTPServer(
        (args.host, args.port), handler_for(dsn, args.dist, args.base)
    )
    server.daemon_threads = True
    with server:
        server.serve_forever()


if __name__ == "__main__":
    main()
