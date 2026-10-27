from datetime import UTC, datetime

from psycopg.pq import TransactionStatus

from scripts.dj1_no2_pilot import PILOT_SERIES, reconcile_dj1_no2_window
from scripts.import_eea import parse_timestamp
from scripts.pilot_report_store import save_report
from scripts.sync_eea_history import insert_history_observations

RNMCA_STREAM = "27064/6404090401"


def sync_dj1_no2_history(
    connection,
    rnmca_data_elements,
    eea_observations,
    *,
    window_start,
    window_end,
    ingested_at,
):
    if (
        not connection.autocommit
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise ValueError("Synchronization requires an idle autocommit connection.")
    role = connection.execute(
        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if role is None or role[0] != "atlas_pilot_ingestor" or role[1] or role[2]:
        raise ValueError(
            "Use the dedicated atlas_pilot_ingestor role without RLS bypass."
        )
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '5min'")
        connection.execute("SELECT pg_advisory_xact_lock(27064, 64040904)")
        streams = connection.execute(
            """SELECT p.code, s.source_id, s.id, d.id, d.canonical_site_id,
                      extensions.ST_X(d.provider_location::extensions.geometry),
                      extensions.ST_Y(d.provider_location::extensions.geometry),
                      s.source_url
               FROM atlas.source_streams s
               JOIN atlas.sources p ON p.id = s.source_id
               JOIN atlas.source_devices d ON d.source_id = s.source_id
                    AND d.external_device_id = s.external_stream_id
               WHERE s.format = 'pilot-replay' AND s.storage_allowed
                 AND s.rights_status = 'verified'
                 AND ((p.code = 'EEA' AND s.external_stream_id = %s)
                   OR (p.code = 'RNMCA' AND s.external_stream_id = %s))
               ORDER BY s.id""",
            (PILOT_SERIES, RNMCA_STREAM),
        ).fetchall()
        by_source = {stream[0]: stream for stream in streams}
        if set(by_source) != {"EEA", "RNMCA"} or len(streams) != 2:
            raise ValueError(
                "Both approved DJ-1/NO2 pilot streams and devices are required."
            )
        site = streams[0][4]
        if site is None or any(
            row[4] != site or row[5] is None or row[6] is None for row in streams
        ):
            raise ValueError(
                "Pilot devices require the same canonical site and known locations."
            )
        previous = connection.execute(
            """SELECT report FROM atlas.pilot_reports
               WHERE (report->>'windowStart')::timestamptz < %s
                 AND (report->>'windowEnd')::timestamptz > %s
               ORDER BY id""",
            (window_end, window_start),
        ).fetchall()
        raw = list(rnmca_data_elements)
        eea = list(eea_observations)
        for (prior,) in previous:
            if (
                prior.get("replayIngestedAt")
                and parse_timestamp(prior["replayIngestedAt"]) > ingested_at
            ):
                raise ValueError("Pilot replay ingestion must not move backwards.")
            for element in prior["currentRnmcaRevisions"]:
                end = datetime.fromtimestamp(element["timestamp"] / 1000, UTC)
                if window_start < end <= window_end:
                    raw.append(element)
            for row in prior["eeaObservations"]:
                candidates = row["candidateIntervals"]
                if not candidates or any(
                    window_start < parse_timestamp(item["observedTo"]) <= window_end
                    for item in candidates
                ):
                    eea.append(row["rawObservation"])
        report = reconcile_dj1_no2_window(
            raw,
            eea,
            window_start=window_start,
            window_end=window_end,
            ingested_at=ingested_at,
        )
        report["replayIngestedAt"] = ingested_at.astimezone(UTC).isoformat()
        report_id = save_report(connection, report)
        for source, stream in by_source.items():
            _, source_id, stream_id, device_id, _, longitude, latitude, source_url = (
                stream
            )
            connection.execute(
                """DELETE FROM atlas.observations
                   WHERE source_id = %s AND stream_id = %s AND device_id = %s
                     AND observed_to > %s AND observed_to <= %s
                     AND parameter_code = 'NO2' AND aggregation_type = 'hour'""",
                (source_id, stream_id, device_id, window_start, window_end),
            )
            rows = []
            for observation in report["observations"]:
                if observation["source"] != source:
                    continue
                row = dict(observation)
                row.update(
                    longitude=observation.get("longitude", longitude),
                    latitude=observation.get("latitude", latitude),
                    status=observation.get("status", observation.get("qaState")),
                    validity=observation.get(
                        "validity", observation.get("qaqcStatusId")
                    ),
                    verification=observation.get(
                        "verification", observation.get("qaqcStateId")
                    ),
                    sourceRecordId=observation.get(
                        "sourceRecordId",
                        str(observation["rawObservation"].get("timestamp")),
                    ),
                    stationId=observation.get("stationId", "27064"),
                    stationName="DJ-1",
                    sourceUrl=observation.get("sourceUrl", source_url),
                    dataCapture=observation.get("dataCapture"),
                    pilotReportId=report_id,
                    qualityDetails={
                        key: value
                        for key, value in observation.items()
                        if key
                        in {
                            "normalizationRule",
                            "temporalStatus",
                            "sourceVersion",
                            "qaState",
                            "qaqcStateId",
                            "qaqcStatusId",
                            "sourceStatus",
                        }
                    },
                )
                rows.append(row)
            insert_history_observations(
                connection, source_id, stream_id, device_id, rows
            )
        return {"reportId": report_id, "observationCount": len(report["observations"])}
