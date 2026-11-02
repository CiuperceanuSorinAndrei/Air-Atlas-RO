import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from scripts import eea_catalog as registry
from scripts import import_eea as importer


def load_json(path):
    return json.loads(
        registry.read_bounded(path), object_pairs_hook=registry.unique_object
    )


def parse_arcgis(body):
    if not isinstance(body, bytes) or len(body) > registry.MAX_DOCUMENT_BYTES:
        raise ValueError("ArcGIS input exceeds the byte limit.")
    document = json.loads(body, object_pairs_hook=registry.unique_object)
    if (
        not isinstance(document, dict)
        or document.get("type") != "FeatureCollection"
        or document.get("exceededTransferLimit")
        or not isinstance(document.get("properties", {}), dict)
        or document.get("properties", {}).get("exceededTransferLimit")
    ):
        raise ValueError("Expected a complete ArcGIS feature collection.")
    features = document.get("features")
    if not isinstance(features, list) or len(features) > 2000:
        raise ValueError("Invalid or excessive ArcGIS features.")
    stations = {}
    for feature in features:
        if not isinstance(feature, dict):
            raise TypeError("Invalid ArcGIS feature.")
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise TypeError("Invalid ArcGIS station properties/geometry.")
        coordinates = geometry.get("coordinates")
        if (
            geometry.get("type") != "Point"
            or not isinstance(coordinates, list)
            or len(coordinates) != 2
        ):
            raise ValueError("Invalid ArcGIS station position.")
        station_id = properties.get("AirQualityStationEoICode")
        if not isinstance(station_id, str) or station_id in stations:
            raise ValueError("Invalid or duplicate ArcGIS station identity.")
        row = {
            "stationId": station_id,
            "stationName": properties.get("AQStationName"),
            "longitude": coordinates[0],
            "latitude": coordinates[1],
        }
        importer.validate_station_metadata(row, station_id)
        stations[station_id] = row
    return stations


def changes(previous, current, key):
    before, after = (
        {key(row): row for row in previous},
        {key(row): row for row in current},
    )
    return {
        "added": [after[k] for k in sorted(after.keys() - before.keys())],
        "removed": [before[k] for k in sorted(before.keys() - after.keys())],
        "changed": [
            {"before": before[k], "after": after[k]}
            for k in sorted(before.keys() & after.keys())
            if before[k] != after[k]
        ],
    }


def build_candidates(
    xml_body,
    source_url,
    reporting_year,
    discovery_body,
    *,
    catalog_version=1,
    inventory_version=1,
    arcgis_body=None,
    previous_catalog=None,
    previous_inventory=None,
):
    importer.validate_dataflow_d_source_url(source_url)
    registry.version(catalog_version)
    registry.version(inventory_version)
    nodes, contents, digest, delivery = importer.parse_dataflow_d_report(
        xml_body, reporting_year
    )
    if not 1 <= len(nodes) <= 2000:
        raise ValueError("Expected a nonempty bounded XML station collection.")
    urls = importer.parse_parquet_urls_payload(discovery_body)
    grouped = importer.group_series_urls(urls)
    other = parse_arcgis(arcgis_body) if arcgis_body is not None else {}
    stations = []
    activity = []
    for node in nodes:
        station_id = importer.dataflow_xml_text(node, "aqd:EUStationCode")
        row = importer.resolve_dataflow_d_station(
            nodes, contents, station_id, source_url, reporting_year, digest
        )
        row.update(
            namespace="RO.ANPM.AQ",
            localId=f"STA-{station_id}",
            metadataEnvelopeUrl=source_url.rsplit("/", 1)[0] + "/",
            metadataDelivery=delivery,
        )
        if station_id in other:
            row["arcgis"] = other[station_id]
        stations.append(row)
        periods = node.findall(
            "ef:operationalActivityPeriod/ef:OperationalActivityPeriod/ef:activityTime/gml:TimePeriod",
            importer.XML_NAMESPACES,
        )
        for period in periods:
            end = period.find("gml:endPosition", importer.XML_NAMESPACES)
            if end is not None and end.text and end.text.strip():
                activity.append(
                    {"stationId": station_id, "reportedActivityEnd": end.text}
                )
    catalog = {
        "schemaVersion": 1,
        "catalogVersion": catalog_version,
        "stations": sorted(stations, key=lambda row: row["stationId"]),
    }
    known = registry.validate_station_catalog(catalog)
    channels = []
    for (station_id, code), streams in sorted(grouped.items()):
        if station_id not in known:
            continue
        channels.append(
            {
                "stationId": station_id,
                "pollutant": importer.POLLUTANT_NAMES[int(code)],
                "streams": [
                    {
                        "samplingPointId": "RO/" + Path(urlsplit(url).path).stem,
                        "sourceUrl": url,
                    }
                    for _, url in sorted(streams, reverse=True)
                ],
            }
        )
    inventory = {
        "schemaVersion": 1,
        "catalogVersion": catalog_version,
        "inventoryVersion": inventory_version,
        "channels": sorted(
            channels, key=lambda row: (row["stationId"], row["pollutant"])
        ),
    }
    registry.validate_channel_inventory(inventory, catalog)
    reconciliation = registry.reconcile_discovery(urls, catalog, inventory)
    if (previous_catalog is None) != (previous_inventory is None):
        raise ValueError("Previous catalog and inventory must be supplied together.")
    baseline = None
    if previous_catalog is not None:
        registry.validate_channel_inventory(previous_inventory, previous_catalog)
        if (
            catalog_version <= previous_catalog["catalogVersion"]
            or inventory_version <= previous_inventory["inventoryVersion"]
        ):
            raise ValueError("Candidate versions must exceed the previous versions.")
        baseline = {
            "previousCatalogVersion": previous_catalog["catalogVersion"],
            "previousInventoryVersion": previous_inventory["inventoryVersion"],
            "stations": changes(
                previous_catalog["stations"],
                catalog["stations"],
                lambda row: row["stationId"],
            ),
            "channels": changes(
                previous_inventory["channels"],
                inventory["channels"],
                lambda row: (row["stationId"], row["pollutant"]),
            ),
            "discoveryAgainstPrevious": registry.reconcile_discovery(
                urls, previous_catalog, previous_inventory
            ),
        }
    discovered_stations = {importer.parse_series_url(url)[0] for url in urls}
    report = {
        "approvalState": "candidate-only",
        "requiresOwnerApproval": True,
        "discoveryCompleteness": "not-established-by-a-single-response",
        "inputs": {
            "xmlSha256": digest,
            "discoverySha256": hashlib.sha256(discovery_body).hexdigest(),
            "arcgisSha256": hashlib.sha256(arcgis_body).hexdigest()
            if arcgis_body is not None
            else None,
            "metadataSourceUrl": source_url,
            "metadataReportingYear": reporting_year,
            "previousCatalogSha256": hashlib.sha256(
                encode(previous_catalog)
            ).hexdigest()
            if previous_catalog
            else None,
            "previousInventorySha256": hashlib.sha256(
                encode(previous_inventory)
            ).hexdigest()
            if previous_inventory
            else None,
        },
        "counts": {
            "xmlStations": len(stations),
            "discoveryStations": len(discovered_stations),
            "discoveryStreams": len(urls),
            "candidateChannels": len(channels),
            "candidateStreams": sum(len(c["streams"]) for c in channels),
            "arcgisStations": len(other),
        },
        "xmlOnlyStations": sorted(known.keys() - discovered_stations),
        "discoveryOnlyStations": sorted(discovered_stations - known.keys()),
        "missingCrossChecks": sorted(known.keys() - other.keys()),
        "arcgisOnlyStations": sorted(other.keys() - known.keys()),
        "nameDifferences": [
            {
                "stationId": sid,
                "xmlName": known[sid]["stationName"],
                "arcgisName": other[sid]["stationName"],
            }
            for sid in sorted(known.keys() & other.keys())
            if known[sid]["stationName"] != other[sid]["stationName"]
        ],
        "reportedActivityEnds": sorted(
            activity, key=lambda row: (row["stationId"], row["reportedActivityEnd"])
        ),
        "reconciliation": reconciliation,
        "baselineDiff": baseline,
    }
    return {
        "catalog-candidate.json": catalog,
        "inventory-candidate.json": inventory,
        "review-report.json": report,
    }


def encode(document):
    return (
        json.dumps(
            document, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True
        )
        + "\n"
    ).encode("utf-8")


def write_files(encoded, output):
    with tempfile.TemporaryDirectory(
        prefix=".eea-candidates-", dir=output.parent
    ) as staging:
        for name, body in encoded.items():
            with (Path(staging) / name).open("xb") as stream:
                stream.write(body)
        output.mkdir()
        try:
            os.rename(staging, output)
        except OSError:
            output.rmdir()
            raise


def main():
    parser = argparse.ArgumentParser(
        description="Build offline EEA review candidates; never approve or publish."
    )
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--reporting-year", required=True, type=int)
    parser.add_argument("--discovery", required=True, type=Path)
    parser.add_argument("--arcgis", type=Path)
    parser.add_argument("--previous-catalog", type=Path)
    parser.add_argument("--previous-inventory", type=Path)
    parser.add_argument("--catalog-version", type=int, default=1)
    parser.add_argument("--inventory-version", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        documents = build_candidates(
            registry.read_bounded(args.xml, 32 * 1024 * 1024),
            args.source_url,
            args.reporting_year,
            registry.read_bounded(args.discovery, 2 * 1024 * 1024),
            catalog_version=args.catalog_version,
            inventory_version=args.inventory_version,
            arcgis_body=registry.read_bounded(args.arcgis) if args.arcgis else None,
            previous_catalog=load_json(args.previous_catalog)
            if args.previous_catalog
            else None,
            previous_inventory=load_json(args.previous_inventory)
            if args.previous_inventory
            else None,
        )
        write_files(
            {name: encode(document) for name, document in documents.items()},
            args.output,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError, AttributeError):
        parser.exit(1, "Invalid candidate inputs or unavailable output directory.\n")
    print(json.dumps(documents["review-report.json"]["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
