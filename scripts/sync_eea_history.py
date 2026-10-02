import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

try:
    from scripts import import_eea as importer
except ModuleNotFoundError:
    import import_eea as importer


def validate_etag(etag: object) -> str:
    if (
        not isinstance(etag, str)
        or len(etag) > 512
        or not re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e]*"|0x[0-9A-Fa-f]+', etag)
    ):
        raise ValueError("Invalid or missing source ETag.")
    return etag


def download_history_version(url: str, etag: str | None) -> tuple[bytes | None, str]:
    importer.parse_series_url(url)
    headers = {}
    if etag is not None:
        validate_etag(etag)
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    for attempt in range(3):
        try:
            body, new_etag = importer.read_http_response(
                request, importer.MAX_PARQUET_BYTES
            )
            return body, validate_etag(new_etag)
        except urllib.error.HTTPError as error:
            if error.code == 304:
                error.close()
                if etag is None:
                    raise ValueError("Unexpected 304 without a stored ETag.") from error
                return None, etag
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            time.sleep(
                min(int(retry_after), 30) if retry_after.isdigit() else 2**attempt
            )
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Download retry limit exhausted.")


def fetch_history_version(
    url: str, etag: str | None, cache_path: Path = importer.STATION_METADATA_CACHE_PATH
) -> tuple[list[dict] | None, str]:
    body, new_etag = download_history_version(url, etag)
    if body is None:
        return None, new_etag
    table = importer.decode_parquet_table(body)
    required = {
        "Value",
        "AggType",
        "Start",
        "End",
        "ResultTime",
        "Pollutant",
        "Samplingpoint",
        "Unit",
        "Validity",
        "Verification",
    }
    if not required <= set(table.column_names):
        raise ValueError("Incomplete history schema.")
    raw_rows = table.to_pylist()
    for row in raw_rows:
        importer.validate_raw_identity(row, url)
        if type(row["Validity"]) is not int or row["Validity"] not in (
            -99,
            -1,
            1,
            2,
            3,
            4,
        ):
            raise ValueError("Unknown source validity code.")
    valid_rows = [row for row in raw_rows if row["Validity"] in (1, 2, 3, 4)]
    if not valid_rows:
        return [], new_etag
    station_id = importer.validate_raw_identity(valid_rows[0], url)
    metadata = importer.get_station_metadata(station_id, cache_path)
    unique = {}
    for raw_row in valid_rows:
        row = importer.normalize_observation(raw_row, metadata, url)
        key = (row["aggregationType"], row["observedFrom"], row["observedTo"])
        if key in unique:
            if {k: v for k, v in row.items() if k != "ingestedAt"} != {
                k: v for k, v in unique[key].items() if k != "ingestedAt"
            }:
                raise ValueError("Conflicting observations for one interval.")
        else:
            unique[key] = row
    return list(unique.values()), new_etag


def sync_stream(connection: psycopg.Connection[tuple], stream_id: int) -> dict:
    if (
        not connection.autocommit
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise ValueError("Synchronization requires an idle autocommit connection.")
    role: tuple[str, bool, bool] | None = connection.execute(
        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if role is None or role[0] != "atlas_ingestor" or role[1] or role[2]:
        raise ValueError("Use the dedicated atlas_ingestor role without RLS bypass.")
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '5min'")
        stream: (
            tuple[int, str, str, str | None, bool, str, str, datetime | None] | None
        ) = connection.execute(
            """SELECT s.source_id, s.external_stream_id, s.source_url, s.etag,
                      s.storage_allowed, s.rights_status, s.format, s.last_synced_at
               FROM atlas.source_streams s JOIN atlas.sources p ON p.id = s.source_id
               WHERE s.id = %s AND p.code = 'EEA' FOR UPDATE OF s""",
            (stream_id,),
        ).fetchone()
        if stream is None:
            raise ValueError("EEA stream is unavailable for ingestion.")
        source_id, external_id, url, etag, allowed, rights, format_name, synced_at = (
            stream
        )
        if not allowed or rights != "verified" or format_name != "parquet":
            raise ValueError("Stream has no verified Parquet storage permission.")
        station_id, pollutant, sequence = importer.parse_series_url(url)
        if external_id != f"RO/SPO-{station_id}_{pollutant}_{sequence}":
            raise ValueError("Stream ID and source URL differ.")
        if etag is not None and synced_at is None:
            raise ValueError("Stored ETag has no completed synchronization.")
        rows, new_etag = fetch_history_version(url, etag)
        if rows is not None:
            device: tuple[int] | None = connection.execute(
                """SELECT id FROM atlas.source_devices
                   WHERE source_id = %s AND external_device_id = %s""",
                (source_id, external_id),
            ).fetchone()
            if device is None and rows:
                device = connection.execute(
                    """INSERT INTO atlas.source_devices(source_id, external_device_id,
                              provider_location, instrument_class, authority_tier)
                       VALUES (%s, %s, extensions.ST_SetSRID(extensions.ST_MakePoint(%s, %s),4326)::extensions.geography,
                               NULL, NULL) RETURNING id""",
                    (source_id, external_id, rows[0]["longitude"], rows[0]["latitude"]),
                ).fetchone()
            connection.execute(
                "DELETE FROM atlas.observations WHERE source_id = %s AND stream_id = %s",
                (source_id, stream_id),
            )
            if rows:
                if device is None:
                    raise ValueError("Source device was not created.")
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO atlas.observations(
                            source_id, stream_id, device_id, parameter_code, aggregation_type,
                            observed_from, observed_to, value, unit, reported_at, ingested_at,
                            validation_state, source_validity, source_verification,
                            source_record_id, observation_location, quality_details)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                             extensions.ST_SetSRID(extensions.ST_MakePoint(%s,%s),4326)::extensions.geography,%s)""",
                        [
                            (
                                source_id,
                                stream_id,
                                device[0],
                                row["pollutant"],
                                row["aggregationType"],
                                row["observedFrom"],
                                row["observedTo"],
                                Decimal(str(row["value"])),
                                row["unit"],
                                row["reportedAt"],
                                row["ingestedAt"],
                                row["status"],
                                str(row["validity"]),
                                str(row["verification"]),
                                row["sourceRecordId"],
                                row["longitude"],
                                row["latitude"],
                                Jsonb(
                                    {
                                        "dataCapture": row["dataCapture"],
                                        "stationId": row["stationId"],
                                        "stationName": row["stationName"],
                                        "sourceUrl": row["sourceUrl"],
                                    }
                                ),
                            )
                            for row in rows
                        ],
                    )
        connection.execute(
            "UPDATE atlas.source_streams SET etag = %s, last_synced_at = %s WHERE id = %s",
            (new_etag, datetime.now(UTC), stream_id),
        )
    return {
        "streamId": stream_id,
        "status": "unchanged" if rows is None else "replaced",
        "observationCount": None if rows is None else len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synchronize approved EEA history streams."
    )
    parser.add_argument("--stream-id", type=int, action="append", required=True)
    args = parser.parse_args()
    dsn = os.environ.get("ATLAS_INGESTION_DSN")
    if not dsn:
        parser.error("ATLAS_INGESTION_DSN is required.")
    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=15, sslmode="verify-full"
    ) as connection:
        available = set(
            importer.fetch_parquet_urls("RO", list(importer.POLLUTANT_NAMES.values()))
        )
        configured = {
            row[0]
            for row in connection.execute(
                """SELECT s.source_url FROM atlas.source_streams s
               JOIN atlas.sources p ON p.id=s.source_id WHERE p.code='EEA' AND s.format='parquet'"""
            ).fetchall()
        }
        print(
            {
                "newSeries": sorted(available - configured),
                "missingSeries": sorted(configured - available),
            }
        )
        failed = 0
        for stream_id in dict.fromkeys(args.stream_id):
            try:
                print(sync_stream(connection, stream_id))
            except (
                psycopg.Error,
                ValueError,
                TypeError,
                KeyError,
                OSError,
                importer.pa.ArrowException,
            ) as error:
                failed += 1
                print(
                    f"Stream {stream_id} failed: {type(error).__name__}",
                    file=sys.stderr,
                )
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
