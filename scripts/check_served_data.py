import argparse
import json
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen

from scripts.import_eea import validate_document


def assess_snapshot(document, now):
    validate_document(document)
    if document["importSummary"]["failed"]:
        raise ValueError("Served snapshot records failed imports.")
    rows = document["observations"]
    recent = [
        row
        for row in rows
        if datetime.fromisoformat(row["observedFrom"]) <= now
        and datetime.fromisoformat(row["reportedAt"]) <= now
        and timedelta(0)
        <= now - datetime.fromisoformat(row["observedTo"])
        < timedelta(hours=6)
    ]
    latest_ingestion = max(datetime.fromisoformat(row["ingestedAt"]) for row in rows)
    ingestion_is_recent = timedelta(0) <= now - latest_ingestion < timedelta(hours=2)
    report = {
        "readings": len(rows),
        "recent_readings": len(recent),
        "stations": len({row["stationId"] for row in rows}),
        "recent_stations": len({row["stationId"] for row in recent}),
        "recent_by_pollutant": {
            pollutant: sum(row["pollutant"] == pollutant for row in recent)
            for pollutant in sorted({row["pollutant"] for row in rows})
        },
        "latest_measurement": max(
            datetime.fromisoformat(row["observedTo"]) for row in rows
        ).isoformat(),
        "latest_ingestion": latest_ingestion.isoformat(),
        "ingestion_age_hours": (now - latest_ingestion).total_seconds() / 3600,
        "ingestion_is_recent": ingestion_is_recent,
    }
    degraded = not recent or not ingestion_is_recent
    return report, degraded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    args = parser.parse_args()
    if not args.url.startswith(
        "https://ciuperceanusorinandrei.github.io/Air-Atlas-RO/"
    ):
        raise ValueError("Monitor accepts only the public Atlas Pages origin/path.")
    request = Request(args.url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=30) as response:
        if (
            response.status != 200
            or response.url.split("?")[0] != args.url.split("?")[0]
        ):
            raise ValueError("Unexpected public snapshot response.")
        payload = response.read(8 * 1024 * 1024 + 1)
    if len(payload) > 8 * 1024 * 1024:
        raise ValueError("Served snapshot exceeds monitor size limit.")
    report, degraded = assess_snapshot(json.loads(payload), datetime.now(UTC))
    print(json.dumps(report, indent=2))
    if degraded:
        raise SystemExit(
            f"Degraded: recent_readings={report['recent_readings']}; "
            f"ingestion_age_hours={report['ingestion_age_hours']:.2f} ore; "
            f"ingestion_is_recent={report['ingestion_is_recent']}."
        )


if __name__ == "__main__":
    main()
