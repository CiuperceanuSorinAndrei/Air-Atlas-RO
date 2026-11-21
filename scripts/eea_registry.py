import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import build_eea_candidates as builder
from scripts import eea_catalog as registry
from scripts import eea_resilience as resilience
from scripts import import_eea as importer


def read_json(path):
    body = registry.read_bounded(path)
    return json.loads(body, object_pairs_hook=registry.unique_object), body


def review_registry(xml_body, catalog, inventory):
    channels = registry.validate_channel_inventory(inventory, catalog)
    stations = registry.validate_station_catalog(catalog)
    if registry.cross_check_findings(catalog):
        raise ValueError(
            "Unresolved authoritative/cross-check geometry requires review."
        )
    first = next(iter(stations.values()))
    year, source = first["metadataReportingYear"], first["metadataSourceUrl"]
    nodes, contents, digest, delivery = importer.parse_dataflow_d_report(xml_body, year)
    ns = importer.XML_NAMESPACES
    for station_id, row in stations.items():
        expected = importer.resolve_dataflow_d_station(
            nodes, contents, station_id, source, year, digest
        )
        if (
            any(row[key] != value for key, value in expected.items())
            or row["metadataDelivery"] != delivery
        ):
            raise ValueError("Catalog differs from its original authoritative XML.")
    root = ET.fromstring(xml_body.decode("utf-8-sig"))
    points = {}
    for node in root.findall("gml:featureMember/aqd:AQD_SamplingPoint", ns):
        native = node.get(f"{{{ns['gml']}}}id")
        points.setdefault(native, []).append(node)
    verified = 0
    for (station_id, _), streams in channels.items():
        for stream in streams:
            native = stream["samplingPointId"].removeprefix("RO/")
            matches = points.get(native, [])
            if len(matches) != 1 or "RO.ANPM.AQ/" + native not in contents:
                raise ValueError(
                    "Expected one sampling point in the reporting delivery."
                )
            node = matches[0]
            identity = "ef:inspireId/base:Identifier/"
            parents = node.findall("ef:broader", ns)
            properties = node.findall(
                "ef:observingCapability/ef:ObservingCapability/ef:observedProperty", ns
            )
            code = int(importer.parse_series_url(stream["sourceUrl"])[1])
            if (
                importer.dataflow_xml_text(node, identity + "base:localId") != native
                or importer.dataflow_xml_text(node, identity + "base:namespace")
                != "RO.ANPM.AQ"
                or len(parents) != 1
                or parents[0].get(f"{{{ns['xlink']}}}href")
                != f"RO.ANPM.AQ/STA-{station_id}"
                or len(properties) != 1
                or properties[0].get(f"{{{ns['xlink']}}}href")
                != f"http://dd.eionet.europa.eu/vocabulary/aq/pollutant/{code}"
            ):
                raise ValueError(
                    "Sampling-point identity, parent or pollutant mismatch."
                )
            ends = node.findall(
                "ef:operationalActivityPeriod/ef:OperationalActivityPeriod/ef:activityTime/gml:TimePeriod/gml:endPosition",
                ns,
            )
            if any(end.text and end.text.strip() for end in ends):
                raise ValueError(
                    "Discovered sampling point has a reported activity end; review required."
                )
            verified += 1
    ended_stations = []
    for node in nodes:
        sid = importer.dataflow_xml_text(node, "aqd:EUStationCode")
        ends = node.findall(
            "ef:operationalActivityPeriod/ef:OperationalActivityPeriod/ef:activityTime/gml:TimePeriod/gml:endPosition",
            ns,
        )
        if any(end.text and end.text.strip() for end in ends):
            ended_stations.append(sid)
            if any(key[0] == sid for key in channels):
                raise ValueError(
                    "Inventory station has a reported activity end; review required."
                )
    used = {key[0] for key in channels}
    return {
        "schemaVersion": 1,
        "scope": "local-trial",
        "readyForLocalCollection": True,
        "reviewBasis": "delegated-technical-review",
        "sourceXmlSha256": digest,
        "sourceXmlUrl": source,
        "metadataReportingYear": year,
        "catalogVersion": catalog["catalogVersion"],
        "inventoryVersion": inventory["inventoryVersion"],
        "verifiedStations": len(stations),
        "verifiedSamplingPoints": verified,
        "missingArcgisCrossChecks": sorted(
            sid for sid, row in stations.items() if "arcgis" not in row
        ),
        "stationsWithoutInventoryChannels": sorted(stations.keys() - used),
        "reportedEndedStations": sorted(ended_stations),
        "dispositions": {
            "missingArcgis": "Retain exact official XML identity/geometry; absent secondary evidence is not agreement or a conflict.",
            "xmlOnlyStations": "Retain in catalog without inventing channels or inferring retirement from discovery absence.",
            "endedStations": "Retain historical catalog identity; no inventory channel approved for these stations.",
            "completeness": "Initial explicitly selected discovery baseline; national provider completeness is not established.",
        },
        "blockingFindings": [],
        "productionActivationAuthorized": False,
    }


def prepare_registry(xml_body, catalog, inventory):
    review = review_registry(xml_body, catalog, inventory)
    documents = {
        "catalog.json": catalog,
        "inventory.json": inventory,
        "review.json": review,
    }
    manifest = {
        "schemaVersion": 1,
        "scope": "local-trial",
        "status": "technically-reviewed",
        "catalogVersion": catalog["catalogVersion"],
        "inventoryVersion": inventory["inventoryVersion"],
        "files": {
            name: hashlib.sha256(builder.encode(document)).hexdigest()
            for name, document in documents.items()
        },
    }
    documents["manifest.json"] = manifest
    return documents


def load_registry(manifest_path):
    manifest, body = read_json(manifest_path)
    registry.fields(
        manifest,
        (
            "schemaVersion",
            "scope",
            "status",
            "catalogVersion",
            "inventoryVersion",
            "files",
        ),
    )
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != 1
        or manifest["scope"] != "local-trial"
        or manifest["status"] != "technically-reviewed"
    ):
        raise ValueError(
            "Expected an explicitly reviewed local-trial registry manifest."
        )
    registry.version(manifest["catalogVersion"])
    registry.version(manifest["inventoryVersion"])
    registry.fields(
        manifest["files"], ("catalog.json", "inventory.json", "review.json")
    )
    documents = {}
    for name, digest in manifest["files"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid registry file SHA-256.")
        raw = registry.read_bounded(manifest_path.parent / name)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Reviewed registry file hash mismatch.")
        documents[name] = json.loads(raw, object_pairs_hook=registry.unique_object)
    catalog, inventory, review = (
        documents[name] for name in ("catalog.json", "inventory.json", "review.json")
    )
    channels = registry.validate_channel_inventory(inventory, catalog)
    if registry.cross_check_findings(catalog):
        raise ValueError("Registry geometry requires review.")
    if (
        catalog["catalogVersion"] != manifest["catalogVersion"]
        or inventory["inventoryVersion"] != manifest["inventoryVersion"]
        or not isinstance(review, dict)
        or type(review.get("schemaVersion")) is not int
        or review["schemaVersion"] != 1
        or review.get("reviewBasis") != "delegated-technical-review"
        or type(review.get("catalogVersion")) is not int
        or type(review.get("inventoryVersion")) is not int
        or any(
            row["metadataSourceSha256"] != review.get("sourceXmlSha256")
            or row["metadataSourceUrl"] != review.get("sourceXmlUrl")
            or row["metadataReportingYear"] != review.get("metadataReportingYear")
            for row in catalog["stations"]
        )
        or review.get("readyForLocalCollection") is not True
        or review.get("scope") != "local-trial"
        or review.get("productionActivationAuthorized") is not False
        or review.get("blockingFindings") != []
        or review.get("catalogVersion") != manifest["catalogVersion"]
        or review.get("inventoryVersion") != manifest["inventoryVersion"]
        or type(review.get("verifiedStations")) is not int
        or review["verifiedStations"] != len(catalog["stations"])
        or type(review.get("verifiedSamplingPoints")) is not int
        or review["verifiedSamplingPoints"]
        != sum(len(streams) for streams in channels.values())
    ):
        raise ValueError("Registry versions or technical review do not agree.")
    return catalog, inventory, hashlib.sha256(body).hexdigest()


def collect_registry_run(
    manifest_path, output, *, discovery_body=None, previous_snapshot=None
):
    if os.path.lexists(output) or not output.parent.is_dir():
        raise ValueError("Choose a new output directory under an existing parent.")
    catalog, inventory, manifest_digest = load_registry(manifest_path)
    previous = None
    previous_body = None
    if previous_snapshot is not None:
        previous, previous_body = read_json(previous_snapshot)
        importer.validate_document(previous)
    urls = (
        importer.parse_parquet_urls_payload(discovery_body)
        if discovery_body is not None
        else importer.fetch_parquet_urls("RO", list(importer.POLLUTANT_NAMES.values()))
    )
    result = registry.collect_catalog_observations(sorted(urls), catalog, inventory)
    collection = result["collection"]
    importer.validate_document(collection)
    selected = {
        (row["stationId"], row["pollutant"]) for row in collection["observations"]
    }
    approved = registry.validate_channel_inventory(inventory, catalog)
    without = [
        {"stationId": sid, "pollutant": pollutant, "reason": "noEligibleObservation"}
        for sid, pollutant in sorted(approved.keys() - selected)
    ]
    report = {
        "schemaVersion": 1,
        "mode": "local-registry-trial",
        "registryManifestSha256": manifest_digest,
        "catalogVersion": catalog["catalogVersion"],
        "inventoryVersion": inventory["inventoryVersion"],
        "approvedChannels": len(approved),
        "eligibleChannels": len(selected),
        "channelsWithoutObservation": without,
        "discovery": result["discovery"],
        "discoveryPayloadSha256": hashlib.sha256(discovery_body).hexdigest()
        if discovery_body is not None
        else None,
        "previousSnapshotSha256": hashlib.sha256(previous_body).hexdigest()
        if previous is not None
        else None,
        "completeness": "Assessed against the fixed reviewed inventory; not national coverage or measurement freshness.",
        "controlledDegradationImplemented": False,
    }
    with tempfile.TemporaryDirectory(
        prefix=".eea-snapshot-validation-", dir=output.parent
    ) as temp:
        path = Path(temp) / "observations.json"
        if previous is not None:
            path.write_bytes(builder.encode(previous))
        importer.write_observation_snapshot(collection, path)
        snapshot = path.read_bytes()
    builder.write_files(
        {
            "observations.json": snapshot,
            "collection-report.json": builder.encode(report),
        },
        output,
    )
    return report


REQUIRED_COLUMNS = {
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


def latest_verdict(rows, latest, station, url):
    start, end = (
        importer.parse_timestamp(latest[key])
        .astimezone(importer.EEA_TIMEZONE)
        .replace(tzinfo=None)
        for key in ("observedFrom", "observedTo")
    )
    matches = [raw for raw in rows if raw["Start"] == start and raw["End"] == end]
    if len(matches) > 1:
        raise ValueError("Accepted interval is not unique in the source.")
    if not matches or matches[0]["Validity"] == -99:
        return None, None
    reported = importer.to_eea_iso(matches[0]["ResultTime"])
    order = importer.parse_timestamp(reported) - importer.parse_timestamp(
        latest["reportedAt"]
    )
    if order < timedelta(0):
        return "stale", None
    if matches[0]["Validity"] == -1:
        return "withdrawn", reported
    if order > timedelta(0):
        return None, None
    observation = importer.normalize_observation(matches[0], station, url)
    if resilience.record_digest(observation) == latest["recordSha256"]:
        return "confirmed", None
    return "conflict", None


def inspect_channel(streams, discovered, station, row, now):
    ordered = sorted(
        streams,
        key=lambda s: importer.parse_series_url(s["sourceUrl"])[2],
        reverse=True,
    )
    result = resilience.finding("empty")
    if ordered[0]["sourceUrl"] not in discovered:
        result["outcome"] = "missing"
        return result
    latest = row["latest"]
    for stream in ordered:
        url = stream["sourceUrl"]
        if url not in discovered:
            break
        try:
            body, etag = importer.fetch_parquet_payload(url)
            table = importer.decode_parquet_table(body)
            if not REQUIRED_COLUMNS <= set(table.column_names):
                raise ValueError("Incomplete controlled-degradation source schema.")
            rows = table.to_pylist()
            for raw in rows:
                importer.validate_raw_identity(raw, url)
                if type(raw["Validity"]) is not int or raw["Validity"] not in (
                    -99,
                    -1,
                    1,
                    2,
                    3,
                    4,
                ):
                    raise ValueError("Unknown source validity code.")
            receipt = {"etag": etag, "payloadSha256": resilience.digest(body)}
            verdict = withdrawn = candidate = rejection = None
            if latest is not None and latest["sourceUrl"] == url:
                verdict, withdrawn = latest_verdict(rows, latest, station, url)
            try:
                raw = importer.select_latest_valid_observation(table)
            except importer.NoValidObservationsError:
                raw = None
            if raw is not None:
                candidate = importer.normalize_observation(raw, station, url)
                candidate["ingestedAt"] = now
                importer.validate_observation(candidate)
                end = importer.parse_timestamp(candidate["observedTo"])
                reported = importer.parse_timestamp(candidate["reportedAt"])
                if latest is not None and verdict != "withdrawn":
                    previous = importer.parse_timestamp(latest["observedTo"])
                    if end < previous:
                        rejection = "unconfirmedRegression"
                    elif end == previous and verdict == "stale":
                        rejection = "staleSourceVersion"
                    elif end == previous and verdict == "conflict":
                        rejection = "versionConflict"
                for withdrawal in row["withdrawals"]:
                    if (
                        rejection is None
                        and all(
                            importer.parse_timestamp(withdrawal[key])
                            == importer.parse_timestamp(candidate[key])
                            for key in ("observedFrom", "observedTo")
                        )
                        and withdrawal["sourceUrl"] == url
                    ):
                        order = reported - importer.parse_timestamp(
                            withdrawal["reportedAt"]
                        )
                        if order < timedelta(0):
                            rejection = "staleSourceVersion"
                        elif order == timedelta(0):
                            rejection = "versionConflict"
            if verdict == "withdrawn":
                result["withdrawal"] = {"reportedAt": withdrawn, **receipt}
                latest = None
            if candidate is None:
                continue
            result["outcome"] = "available"
            if rejection is not None:
                result["rejections"] = [rejection]
            else:
                result.update(candidate=candidate, receipt=receipt)
                if verdict == "confirmed" and end == previous:
                    result["latestReceipt"] = receipt
            break
        except urllib.error.HTTPError as error:
            result.update(
                outcome="failed", transient=error.code in (429, 500, 502, 503, 504)
            )
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            result.update(outcome="failed", transient=True)
            break
        except (importer.pa.ArrowException, ValueError, TypeError, KeyError, OSError):
            result.update(outcome="failed", transient=False)
            break
    return result


def inspect_channels(urls, catalog, inventory, previous, quarantined, now):
    approved = registry.validate_channel_inventory(inventory, catalog)
    stations = registry.validate_station_catalog(catalog)
    discovered = set(urls)
    prior = {(row["stationId"], row["pollutant"]): row for row in previous["channels"]}
    return {
        key: resilience.finding("quarantined")
        if key[0] in quarantined
        else inspect_channel(streams, discovered, stations[key[0]], prior[key], now)
        for key, streams in approved.items()
    }


def collect_controlled_run(
    manifest_path,
    output,
    *,
    initialize=False,
    previous_run=None,
    previous_snapshot=None,
    discovery_body=None,
    authority_evidence=None,
    now=None,
):
    if initialize == (previous_run is not None) or (
        previous_snapshot is not None and not initialize
    ):
        raise ValueError("Choose explicit initialization or one complete previous run.")
    if os.path.lexists(output) or not output.parent.is_dir():
        raise ValueError("Choose a new output directory under an existing parent.")
    for source in (manifest_path.parent, previous_run):
        if source is not None and source.resolve() in output.resolve().parents:
            raise ValueError("Degradation output cannot be inside its input bundle.")
    catalog, inventory, manifest_digest = load_registry(manifest_path)
    now = datetime.now(UTC).isoformat() if now is None else now
    if initialize:
        snapshot = (
            registry.read_bounded(previous_snapshot)
            if previous_snapshot is not None
            else None
        )
        previous = resilience.initial_state(
            catalog, inventory, manifest_digest, snapshot, now
        )
        previous_hash = None
    else:
        previous, snapshot, previous_hash = resilience.read_run(
            previous_run, catalog, inventory, manifest_digest
        )
    if importer.parse_timestamp(now) < importer.parse_timestamp(
        previous["generatedAt"]
    ):
        raise ValueError("Degradation-state clock moved backwards.")
    quarantine = (
        resilience.apply_evidence(
            previous["quarantine"], authority_evidence, catalog, manifest_digest, now
        )
        if authority_evidence is not None
        else previous["quarantine"]
    )
    try:
        urls = (
            importer.parse_parquet_urls_payload(discovery_body)
            if discovery_body is not None
            else importer.fetch_parquet_urls(
                "RO", list(importer.POLLUTANT_NAMES.values())
            )
        )
        discovery = registry.reconcile_discovery(urls, catalog, inventory)
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ):
        discovery = None
        findings = None
    else:
        findings = inspect_channels(
            urls,
            catalog,
            inventory,
            previous,
            {row["stationId"] for row in quarantine},
            now,
        )
    state, snapshot, publication = resilience.advance_state(
        previous,
        catalog,
        inventory,
        manifest_digest,
        snapshot,
        now,
        previous_hash,
        discovery,
        findings,
        quarantine,
    )
    report = resilience.report_for(state, discovery, findings, publication)
    resilience.write_run(output, state, report, snapshot)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Prepare technically reviewed registries for explicit local trials only."
    )
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        catalog, _ = read_json(args.catalog)
        inventory, _ = read_json(args.inventory)
        documents = prepare_registry(
            registry.read_bounded(args.xml, 32 * 1024 * 1024), catalog, inventory
        )
        builder.write_files(
            {name: builder.encode(document) for name, document in documents.items()},
            args.output,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        parser.exit(1, "Registry review failed; no complete reviewed bundle written.\n")
    print(
        "Technical review complete for local trial; production activation remains unauthorized."
    )


if __name__ == "__main__":
    main()
