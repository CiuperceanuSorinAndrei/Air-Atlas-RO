import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


def save_report(connection: psycopg.Connection, report: dict, *, schema="atlas") -> int:
    table = sql.Identifier(schema, "pilot_reports")
    row = connection.execute(
        sql.SQL(
            "INSERT INTO {} (report) VALUES (%s) "
            "ON CONFLICT (content_hash) DO NOTHING RETURNING id"
        ).format(table),
        (Jsonb(report),),
    ).fetchone()
    if row is None:
        row = connection.execute(
            sql.SQL(
                "SELECT id FROM {} "
                "WHERE content_hash = sha256(jsonb_send(%s::jsonb)) AND report = %s::jsonb"
            ).format(table),
            (Jsonb(report), Jsonb(report)),
        ).fetchone()
    if row is None:
        raise ValueError("Pilot report content hash conflict or concurrent removal.")
    return row[0]
