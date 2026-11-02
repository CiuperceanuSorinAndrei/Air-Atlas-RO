import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts import build_eea_candidates as builder
from scripts import import_eea as importer

DISCOVERY_URL = (
    "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
)
ARCGIS_URL = (
    "https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/AirQualityDownloadServiceEUMonitoringStations/MapServer/0/query?"
    + urllib.parse.urlencode(
        {
            "where": "AirQualityStationEoICode LIKE 'RO%'",
            "outFields": "AirQualityStationEoICode,AQStationName",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": 2000,
        }
    )
)


def capture_sources(source_url, reporting_year):
    importer.validate_dataflow_d_source_url(source_url)
    payload = {
        "countries": ["RO"],
        "cities": [],
        "pollutants": list(importer.POLLUTANT_NAMES.values()),
        "dataset": 1,
        "source": "Atlasul Aerului Importer",
    }
    requests = [
        ("dataflow-d.xml", urllib.request.Request(source_url), 32 * 1024 * 1024),
        (
            "discovery.txt",
            urllib.request.Request(
                DISCOVERY_URL,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            ),
            2 * 1024 * 1024,
        ),
        ("arcgis.geojson", urllib.request.Request(ARCGIS_URL), 8 * 1024 * 1024),
        (
            "envelope.html",
            urllib.request.Request(source_url.rsplit("/", 1)[0] + "/"),
            2 * 1024 * 1024,
        ),
    ]
    files, manifest = {}, []
    for name, request, limit in requests:
        body, etag = importer.read_http_response(request, limit)
        files[name] = body
        manifest.append(
            {
                "file": name,
                "url": request.full_url,
                "method": request.get_method(),
                "requestBody": request.data.decode() if request.data else None,
                "retrievedAt": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "etag": etag,
            }
        )
    builder.build_candidates(
        files["dataflow-d.xml"],
        source_url,
        reporting_year,
        files["discovery.txt"],
        arcgis_body=files["arcgis.geojson"],
    )
    files["capture.json"] = builder.encode(manifest)
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Capture bounded public EEA inputs; no measurements or publication."
    )
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--reporting-year", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.exit(1, "Capture output directory already exists.\n")
    try:
        files = capture_sources(args.source_url, args.reporting_year)
        builder.write_files(files, args.output)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        AttributeError,
        urllib.error.URLError,
    ):
        parser.exit(1, "Capture failed; no complete source bundle written.\n")
    print("Captured source evidence; candidates still require review.")


if __name__ == "__main__":
    main()
