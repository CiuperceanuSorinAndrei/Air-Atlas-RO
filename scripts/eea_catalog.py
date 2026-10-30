import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts import import_eea as importer

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DISCOVERY_URLS = 16_000


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise ValueError("Missing catalog or inventory fields.")
    if value.keys() - set(required) - set(optional):
        raise ValueError("Unknown catalog or inventory fields.")


def version(value):
    if type(value) is not int or value < 1:
        raise ValueError("Version must be a positive integer.")


def items(value, maximum):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError("Expected a nonempty bounded list.")
    return value


def validate_station_catalog(document):
    fields(document, ("schemaVersion", "catalogVersion", "stations"))
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise ValueError("Unsupported station catalog schema.")
    version(document["catalogVersion"])
    stations = {}
    for row in items(document["stations"], 2000):
        fields(
            row,
            (
                "stationId",
                "stationName",
                "latitude",
                "longitude",
                "namespace",
                "localId",
                "metadataSourceUrl",
                "metadataEnvelopeUrl",
                "metadataReportingYear",
                "metadataSourceSha256",
                "metadataDelivery",
            ),
            ("arcgis",),
        )
        station_id = row["stationId"]
        if not isinstance(station_id, str):
            raise TypeError("Station ID must be text.")
        importer.validate_station_metadata(row, station_id)
        if station_id in stations:
            raise ValueError("Duplicate catalog station.")
        if row["namespace"] != "RO.ANPM.AQ" or row["localId"] != f"STA-{station_id}":
            raise ValueError("Catalog XML station identity mismatch.")
        url = row["metadataSourceUrl"]
        importer.validate_https_url(url, {"cdr.eionet.europa.eu"})
        parsed = urlsplit(url)
        if parsed.query or not re.fullmatch(
            r"/ro/eu/aqd/d/env[a-z0-9]+/[A-Za-z0-9_-]+\.xml", parsed.path
        ):
            raise ValueError("Invalid catalog XML file URL.")
        if row["metadataEnvelopeUrl"] != url.rsplit("/", 1)[0] + "/":
            raise ValueError("Catalog XML envelope mismatch.")
        year = row["metadataReportingYear"]
        if type(year) is not int or not 2013 <= year <= datetime.now(UTC).year:
            raise ValueError("Invalid catalog reporting year.")
        digest = row["metadataSourceSha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid catalog XML SHA-256.")
        delivery = row["metadataDelivery"]
        fields(delivery, ("headerId", "reportingPeriodStart", "reportingPeriodEnd"))
        importer.require_text(delivery["headerId"], "Reporting header ID")
        for key, expected in (
            ("reportingPeriodStart", year),
            ("reportingPeriodEnd", year + 1),
        ):
            stamp = importer.parse_timestamp(delivery[key])
            if (
                stamp.year,
                stamp.month,
                stamp.day,
                stamp.hour,
                stamp.minute,
                stamp.second,
                stamp.microsecond,
            ) != (expected, 1, 1, 0, 0, 0, 0):
                raise ValueError("Catalog XML reporting period mismatch.")
        begin = importer.parse_timestamp(delivery["reportingPeriodStart"])
        end = importer.parse_timestamp(delivery["reportingPeriodEnd"])
        if begin.utcoffset() != end.utcoffset():
            raise ValueError("Catalog reporting period offsets must agree.")
        if "arcgis" in row:
            fields(row["arcgis"], ("stationId", "stationName", "longitude", "latitude"))
            importer.validate_station_metadata(row["arcgis"], station_id)
        stations[station_id] = row
    return stations


def validate_channel_inventory(document, catalog):
    stations = validate_station_catalog(catalog)
    fields(
        document, ("schemaVersion", "inventoryVersion", "catalogVersion", "channels")
    )
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise ValueError("Unsupported channel inventory schema.")
    version(document["inventoryVersion"])
    version(document["catalogVersion"])
    if document["catalogVersion"] != catalog["catalogVersion"]:
        raise ValueError("Inventory references a different catalog version.")
    channels = {}
    for row in items(document["channels"], 12_000):
        fields(row, ("stationId", "pollutant", "streams"))
        if not isinstance(row["stationId"], str) or row["stationId"] not in stations:
            raise ValueError("Inventory station is absent from the catalog.")
        if (
            not isinstance(row["pollutant"], str)
            or row["pollutant"] not in importer.POLLUTANT_NAMES.values()
        ):
            raise ValueError("Invalid inventory pollutant.")
        key = (row["stationId"], row["pollutant"])
        if key in channels:
            raise ValueError("Duplicate inventory channel.")
        sequences = set()
        for stream in items(row["streams"], 32):
            fields(stream, ("samplingPointId", "sourceUrl"))
            station, code, sequence = importer.parse_series_url(stream["sourceUrl"])
            native_id = "RO/" + Path(urlsplit(stream["sourceUrl"]).path).stem
            if (station, importer.POLLUTANT_NAMES[int(code)]) != key or stream[
                "samplingPointId"
            ] != native_id:
                raise ValueError("Inventory stream identity mismatch.")
            if sequence in sequences:
                raise ValueError("Duplicate inventory sequence.")
            sequences.add(sequence)
        channels[key] = row["streams"]
    return channels


def cross_check_findings(catalog):
    stations = validate_station_catalog(catalog)
    findings = []
    for station_id, row in stations.items():
        other = row.get("arcgis")
        if other is None:
            continue
        lat1, lat2 = map(math.radians, (row["latitude"], other["latitude"]))
        dlat = lat2 - lat1
        dlon = math.radians(other["longitude"] - row["longitude"])
        hav = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        distance = 2 * 6_371_008.8 * math.asin(math.sqrt(min(1, max(0, hav))))
        if distance > 10:
            findings.append({"stationId": station_id, "distanceMeters": distance})
    return findings


def reconcile_discovery(urls, catalog, inventory):
    channels = validate_channel_inventory(inventory, catalog)
    if not isinstance(urls, list) or len(urls) > MAX_DISCOVERY_URLS:
        raise ValueError("Discovery URL limit exceeded.")
    expected = {
        s["sourceUrl"]: key for key, streams in channels.items() for s in streams
    }
    discovered = set()
    unknown_channels = set()
    unknown_streams = []
    for url in urls:
        station, code, _ = importer.parse_series_url(url)
        if url in discovered:
            raise ValueError("Duplicate discovery URL.")
        discovered.add(url)
        key = (station, importer.POLLUTANT_NAMES[int(code)])
        if url not in expected:
            unknown_streams.append(url)
            if key not in channels:
                unknown_channels.add(key)
    missing = sorted(set(expected) - discovered)
    present = {expected[url] for url in discovered & expected.keys()}
    return {
        "catalogVersion": catalog["catalogVersion"],
        "inventoryVersion": inventory["inventoryVersion"],
        "matchedStreams": sorted(discovered & expected.keys()),
        "missingChannels": [list(key) for key in sorted(channels.keys() - present)],
        "missingStreams": missing,
        "unregisteredChannels": [list(key) for key in sorted(unknown_channels)],
        "unregisteredStreams": sorted(unknown_streams),
        "crossCheckFindings": cross_check_findings(catalog),
    }


def collect_catalog_observations(urls, catalog, inventory):
    report = reconcile_discovery(urls, catalog, inventory)
    if (
        report["missingChannels"]
        or report["missingStreams"]
        or report["crossCheckFindings"]
    ):
        raise ValueError(
            "Catalog collection requires complete reviewed discovery and geometry."
        )
    result = importer.collect_observations(
        report["matchedStreams"], fail_fast=True, station_catalog=catalog
    )
    return {"collection": result, "discovery": report}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def read_bounded(path, limit=MAX_DOCUMENT_BYTES):
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Catalog input exceeds the byte limit.")
    return body


def main():
    parser = argparse.ArgumentParser(
        description="Validate EEA registries offline; never publish observations."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--discovery", type=Path)
    args = parser.parse_args()
    try:
        catalog = json.loads(
            read_bounded(args.catalog), object_pairs_hook=unique_object
        )
        inventory = json.loads(
            read_bounded(args.inventory), object_pairs_hook=unique_object
        )
        channels = validate_channel_inventory(inventory, catalog)
        if args.discovery:
            urls = importer.parse_parquet_urls_payload(
                read_bounded(args.discovery, 2 * 1024 * 1024)
            )
            report = reconcile_discovery(urls, catalog, inventory)
            needs_review = any(
                report[key]
                for key in (
                    "missingChannels",
                    "missingStreams",
                    "unregisteredStreams",
                    "crossCheckFindings",
                )
            )
        else:
            report = {
                "stations": len(catalog["stations"]),
                "channels": len(channels),
                "crossCheckFindings": cross_check_findings(catalog),
            }
            needs_review = bool(report["crossCheckFindings"])
        print(json.dumps(report, indent=2, allow_nan=False))
        return 2 if needs_review else 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        parser.exit(1, "Invalid catalog, inventory or discovery input.\n")


if __name__ == "__main__":
    raise SystemExit(main())
