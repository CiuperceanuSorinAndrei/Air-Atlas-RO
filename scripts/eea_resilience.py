import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, timedelta
from pathlib import Path

from scripts import build_eea_candidates as builder
from scripts import eea_catalog as catalog_tools
from scripts import import_eea as importer

MODE = "controlled-degradation"
REJECTIONS = {"versionConflict", "staleSourceVersion", "unconfirmedRegression"}
REASONS = REJECTIONS | {
    "notInspected",
    "missingRegisteredStream",
    "sourceFailure",
    "invalidSource",
    "unconfirmedObservationLoss",
    "observationWithdrawn",
    "retiredInterval",
    "quarantined",
}
OUTCOMES = {"available", "empty", "failed", "missing", "quarantined"}
DECISIONS = {"normal", "corrective", "blocked"}
BLOCKERS = {"globalDiscoveryFailure", "budgetExceeded", "emptyNormalBatch"}
FALLBACK = timedelta(hours=24)
MAX_WITHDRAWALS = 8
MAX_QUARANTINE = 64
VERSION = ("sourceUrl", "observedFrom", "observedTo", "reportedAt")
RECEIPT = ("etag", "payloadSha256", "confirmedAt")
EVIDENCE = (
    "stationId",
    "kind",
    "sourceUrl",
    "reportingYear",
    "xmlSha256",
    "headerId",
    "reviewedAt",
)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def checked_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid degradation-state SHA-256.")


def record_digest(row):
    record = {
        key: row.get(key)
        for key in (
            "sourceUrl",
            "value",
            "unit",
            "validity",
            "verification",
            "aggregationType",
            "sourceRecordId",
            "dataCapture",
        )
    }
    for key in VERSION[1:]:
        record[key] = importer.parse_timestamp(row[key]).astimezone(UTC).isoformat()
    return digest(builder.encode(record))


def latest_from(row, receipt, confirmed):
    return {
        **{key: row[key] for key in VERSION},
        "recordSha256": record_digest(row),
        "etag": receipt["etag"] if receipt else None,
        "payloadSha256": receipt["payloadSha256"] if receipt else None,
        "confirmedAt": confirmed if receipt else None,
    }


def finding(outcome, **details):
    result = {
        "outcome": outcome,
        "transient": False,
        "candidate": None,
        "receipt": None,
        "latestReceipt": None,
        "withdrawal": None,
        "rejections": [],
    }
    if details.keys() - result.keys():
        raise ValueError("Unknown channel finding fields.")
    result.update(details)
    return result


def encode_snapshot(document):
    return (
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def snapshot_rows(body, approved):
    if body is None:
        return {}
    document = json.loads(body, object_pairs_hook=catalog_tools.unique_object)
    importer.validate_document(document)
    rows = {}
    for row in document["observations"]:
        key = row["stationId"], row["pollutant"]
        if key not in approved or row["sourceUrl"] not in {
            stream["sourceUrl"] for stream in approved[key]
        }:
            raise ValueError("Accepted snapshot contains an unapproved stream.")
        rows[key] = row
    return rows


def checked_receipt(row, generated, required):
    confirmed = row["confirmedAt"]
    if confirmed is None and not required:
        if row["etag"] is not None or row["payloadSha256"] is not None:
            raise ValueError("Unconfirmed readings cannot carry source receipts.")
        return
    if importer.parse_timestamp(confirmed) > generated:
        raise ValueError("Source confirmation cannot follow its state.")
    checked_digest(row["payloadSha256"])
    if row["etag"] is not None:
        importer.require_text(row["etag"], "Source ETag")


def checked_version(row, streams, extra, generated, required):
    catalog_tools.fields(row, VERSION + RECEIPT + extra)
    if row["sourceUrl"] not in {stream["sourceUrl"] for stream in streams}:
        raise ValueError("Source version references an unapproved stream.")
    start, end, _ = (importer.parse_timestamp(row[key]) for key in VERSION[1:])
    if start >= end:
        raise ValueError("Source version interval is empty.")
    checked_receipt(row, generated, required)


def checked_quarantine(quarantine, catalog, generated):
    stations = catalog_tools.validate_station_catalog(catalog)
    if not isinstance(quarantine, list) or len(quarantine) > MAX_QUARANTINE:
        raise ValueError("Invalid station quarantine list.")
    for row in quarantine:
        catalog_tools.fields(row, (*EVIDENCE, "since"))
        station = stations.get(row["stationId"])
        if station is None or row["kind"] not in ("identity", "geometry"):
            raise ValueError("Quarantine requires a catalog station and conflict kind.")
        importer.validate_https_url(row["sourceUrl"], {"cdr.eionet.europa.eu"})
        checked_digest(row["xmlSha256"])
        importer.require_text(row["headerId"], "Reporting header ID")
        if (
            type(row["reportingYear"]) is not int
            or row["xmlSha256"] == station["metadataSourceSha256"]
            or not importer.parse_timestamp(row["reviewedAt"])
            <= importer.parse_timestamp(row["since"])
            <= generated
        ):
            raise ValueError("Quarantine requires distinct reviewed evidence.")
    ids = [row["stationId"] for row in quarantine]
    if ids != sorted(set(ids)):
        raise ValueError("Quarantined stations must be unique and ordered.")
    return set(ids)


def apply_evidence(quarantine, body, catalog, manifest_digest, now):
    document = json.loads(body, object_pairs_hook=catalog_tools.unique_object)
    catalog_tools.fields(
        document, ("schemaVersion", "registryManifestSha256", "findings")
    )
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or document["registryManifestSha256"] != manifest_digest
    ):
        raise ValueError("Authority evidence requires the same reviewed registry.")
    current = {row["stationId"]: row for row in quarantine}
    seen = set()
    for row in catalog_tools.items(document["findings"], MAX_QUARANTINE):
        catalog_tools.fields(row, (*EVIDENCE, "status"))
        station = row["stationId"]
        if station in seen or row["status"] not in ("quarantined", "resolved"):
            raise ValueError("Invalid or duplicate authority finding.")
        seen.add(station)
        if row["status"] == "resolved":
            if station not in current:
                raise ValueError("Resolved station is not quarantined.")
            del current[station]
        else:
            current[station] = {
                **{key: row[key] for key in EVIDENCE},
                "since": current.get(station, {}).get("since", now),
            }
    result = [current[station] for station in sorted(current)]
    checked_quarantine(result, catalog, importer.parse_timestamp(now))
    return result


def validate_state(state, catalog, inventory, manifest_digest, snapshot_body):
    catalog_tools.fields(
        state,
        (
            "schemaVersion",
            "mode",
            "registryManifestSha256",
            "catalogVersion",
            "inventoryVersion",
            "generation",
            "generatedAt",
            "previousRunSha256",
            "acceptedSnapshotSha256",
            "runIncident",
            "quarantine",
            "channels",
        ),
    )
    approved = catalog_tools.validate_channel_inventory(inventory, catalog)
    checked_digest(manifest_digest)
    if (
        type(state["schemaVersion"]) is not int
        or state["schemaVersion"] != 3
        or state["mode"] != MODE
        or state["registryManifestSha256"] != manifest_digest
        or type(state["catalogVersion"]) is not int
        or state["catalogVersion"] != catalog["catalogVersion"]
        or type(state["inventoryVersion"]) is not int
        or state["inventoryVersion"] != inventory["inventoryVersion"]
    ):
        raise ValueError("Degradation state requires the same reviewed registry.")
    generation = state["generation"]
    if type(generation) is not int or generation < 0:
        raise ValueError("Invalid degradation-state generation.")
    if generation > 1:
        checked_digest(state["previousRunSha256"])
    elif state["previousRunSha256"] is not None:
        raise ValueError("Initial degradation state has no predecessor.")
    generated = importer.parse_timestamp(state["generatedAt"])
    expected_hash = digest(snapshot_body) if snapshot_body is not None else None
    if state["acceptedSnapshotSha256"] != expected_hash:
        raise ValueError("Accepted snapshot does not match degradation state.")
    accepted = snapshot_rows(snapshot_body, approved)
    quarantined = checked_quarantine(state["quarantine"], catalog, generated)
    if state["runIncident"] not in (None, "globalDiscoveryFailure"):
        raise ValueError("Unknown degradation run incident.")
    if not isinstance(state["channels"], list) or len(state["channels"]) != len(
        approved
    ):
        raise ValueError("Degradation state must retain the entire approved inventory.")
    seen = set()
    for row in state["channels"]:
        catalog_tools.fields(
            row,
            (
                "stationId",
                "pollutant",
                "hasObserved",
                "status",
                "reasons",
                "degradedSince",
                "latest",
                "withdrawals",
                "retiredBefore",
            ),
        )
        if not isinstance(row["stationId"], str) or not isinstance(
            row["pollutant"], str
        ):
            raise TypeError("Degradation channel identity must be text.")
        key = row["stationId"], row["pollutant"]
        if key not in approved or key in seen:
            raise ValueError("Unknown or duplicate degradation channel.")
        seen.add(key)
        latest = row["latest"]
        if latest is not None:
            checked_version(latest, approved[key], ("recordSha256",), generated, False)
            checked_digest(latest["recordSha256"])
        withdrawals = row["withdrawals"]
        if not isinstance(withdrawals, list) or len(withdrawals) > MAX_WITHDRAWALS:
            raise ValueError("Invalid withdrawal evidence.")
        for withdrawal in withdrawals:
            checked_version(withdrawal, approved[key], (), generated, True)
        retired = row["retiredBefore"]
        if not isinstance(retired, dict) or not retired.keys() <= {
            stream["sourceUrl"] for stream in approved[key]
        }:
            raise ValueError("Invalid retired withdrawal floor.")
        floors = {
            url: importer.parse_timestamp(value) for url, value in retired.items()
        }
        intervals = [tuple(w[k] for k in VERSION[:3]) for w in withdrawals]
        if (
            len(set(intervals)) != len(intervals)
            or any(
                version["sourceUrl"] in floors
                and importer.parse_timestamp(version["observedTo"])
                <= floors[version["sourceUrl"]]
                for version in (*withdrawals, *([latest] if latest else []))
            )
            or any(
                latest is not None
                and tuple(latest[k] for k in VERSION[:3]) == interval
                and importer.parse_timestamp(latest["reportedAt"])
                <= importer.parse_timestamp(w["reportedAt"])
                for w, interval in zip(withdrawals, intervals, strict=True)
            )
        ):
            raise ValueError("Duplicate, retired or revived withdrawal evidence.")
        if key in accepted and (
            key[0] in quarantined
            or latest is None
            or any(latest[k] != accepted[key][k] for k in VERSION)
            or latest["recordSha256"] != record_digest(accepted[key])
        ):
            raise ValueError("Accepted reading does not match its source version.")
        if type(row["hasObserved"]) is not bool or (
            (key in accepted or latest is not None or withdrawals)
            and not row["hasObserved"]
        ):
            raise ValueError(
                "Accepted observations cannot become never-observed channels."
            )
        reasons = row["reasons"]
        if (
            not isinstance(reasons, list)
            or any(
                not isinstance(reason, str) or reason not in REASONS
                for reason in reasons
            )
            or reasons != sorted(set(reasons))
            or (reasons == ["quarantined"]) != (key[0] in quarantined)
            or ("quarantined" in reasons) != (key[0] in quarantined)
        ):
            raise ValueError("Invalid degradation reasons.")
        if row["status"] == "degraded":
            if (
                not reasons
                or importer.parse_timestamp(row["degradedSince"]) > generated
            ):
                raise ValueError(
                    "Degraded channels require reasons and an elapsed start time."
                )
        elif row["status"] in ("available", "healthy-empty"):
            if reasons or row["degradedSince"] is not None:
                raise ValueError(
                    "Recovered channels cannot retain degradation reasons."
                )
            if row["hasObserved"] != (row["status"] == "available"):
                raise ValueError(
                    "Previously observed channels cannot recover as healthy-empty."
                )
        else:
            raise ValueError("Unknown degradation channel status.")
    return approved


def initial_state(catalog, inventory, manifest_digest, snapshot_body, now):
    approved = catalog_tools.validate_channel_inventory(inventory, catalog)
    accepted = snapshot_rows(snapshot_body, approved)
    state = {
        "schemaVersion": 3,
        "mode": MODE,
        "registryManifestSha256": manifest_digest,
        "catalogVersion": catalog["catalogVersion"],
        "inventoryVersion": inventory["inventoryVersion"],
        "generation": 0,
        "generatedAt": now,
        "previousRunSha256": None,
        "acceptedSnapshotSha256": digest(snapshot_body)
        if snapshot_body is not None
        else None,
        "runIncident": None,
        "quarantine": [],
        "channels": [
            {
                "stationId": sid,
                "pollutant": pollutant,
                "hasObserved": (sid, pollutant) in accepted,
                "status": "degraded",
                "reasons": ["notInspected"],
                "degradedSince": now,
                "latest": latest_from(accepted[sid, pollutant], None, None)
                if (sid, pollutant) in accepted
                else None,
                "withdrawals": [],
                "retiredBefore": {},
            }
            for sid, pollutant in sorted(approved)
        ],
    }
    validate_state(state, catalog, inventory, manifest_digest, snapshot_body)
    return state


def retire_withdrawals(row):
    def end(withdrawal):
        return importer.parse_timestamp(withdrawal["observedTo"])

    floors = row["retiredBefore"]
    kept = [
        w
        for w in row["withdrawals"]
        if w["sourceUrl"] not in floors
        or end(w) > importer.parse_timestamp(floors[w["sourceUrl"]])
    ]
    while len(kept) > MAX_WITHDRAWALS:
        oldest = min(kept, key=lambda w: (end(w), w["sourceUrl"]))
        kept.remove(oldest)
        floors[oldest["sourceUrl"]] = oldest["observedTo"]
    row["withdrawals"] = kept


def budget(state):
    degraded = [row for row in state["channels"] if row["status"] == "degraded"]
    stations = {row["stationId"] for row in degraded}
    return {
        "degradedChannels": len(degraded),
        "degradedStations": len(stations),
        "withinBudget": len(degraded) <= 6 and len(stations) <= 3,
    }


def checked_finding(result, key, streams, now):
    catalog_tools.fields(result, finding("empty"))
    outcome, candidate = result["outcome"], result["candidate"]
    rejections = result["rejections"]
    if (
        not isinstance(outcome, str)
        or outcome not in OUTCOMES - {"quarantined"}
        or type(result["transient"]) is not bool
        or not isinstance(rejections, list)
        or not set(rejections) <= REJECTIONS
        or (outcome == "available") != (candidate is not None or bool(rejections))
        or (candidate is not None and bool(rejections))
        or (result["transient"] and outcome != "failed")
    ):
        raise ValueError("Unknown channel collection outcome.")
    for name in ("receipt", "latestReceipt", "withdrawal"):
        receipt = result[name]
        if receipt is None:
            continue
        extra = ("reportedAt",) if name == "withdrawal" else ()
        catalog_tools.fields(receipt, ("etag", "payloadSha256", *extra))
        checked_digest(receipt["payloadSha256"])
        if receipt["etag"] is not None:
            importer.require_text(receipt["etag"], "Source ETag")
    if candidate is not None:
        importer.validate_observation(candidate)
        if (
            (candidate["stationId"], candidate["pollutant"]) != key
            or candidate["sourceUrl"] not in {stream["sourceUrl"] for stream in streams}
            or candidate["ingestedAt"] != now
            or result["receipt"] is None
        ):
            raise ValueError("Candidate reading requires its approved source receipt.")


def advance_state(
    previous,
    catalog,
    inventory,
    manifest_digest,
    snapshot_body,
    now,
    previous_run_hash,
    discovery,
    findings,
    quarantine=None,
):
    approved = validate_state(
        previous, catalog, inventory, manifest_digest, snapshot_body
    )
    moment = importer.parse_timestamp(now)
    if moment < importer.parse_timestamp(previous["generatedAt"]):
        raise ValueError("Degradation-state clock moved backwards.")
    stations = catalog_tools.validate_station_catalog(catalog)
    accepted = snapshot_rows(snapshot_body, approved)
    state = copy.deepcopy(previous)
    state.update(
        generation=previous["generation"] + 1,
        generatedAt=now,
        previousRunSha256=previous_run_hash,
        runIncident="globalDiscoveryFailure" if discovery is None else None,
    )
    if quarantine is not None:
        state["quarantine"] = copy.deepcopy(quarantine)
    quarantined = checked_quarantine(state["quarantine"], catalog, moment)
    if discovery is None:
        if findings is not None:
            raise ValueError(
                "Global discovery failure cannot contain channel outcomes."
            )
    else:
        expected_discovery = catalog_tools.reconcile_discovery(
            discovery["matchedStreams"] + discovery["unregisteredStreams"],
            catalog,
            inventory,
        )
        if discovery != expected_discovery:
            raise ValueError("Discovery report does not match the approved inventory.")
        if not isinstance(findings, dict) or findings.keys() != approved.keys():
            raise ValueError("Every approved channel requires exactly one outcome.")
        urls = set(discovery["matchedStreams"])
        missing = set(discovery["missingStreams"])
    published, exclusions, proposals = {}, {}, {}
    retained, omitted = [], []
    for row in state["channels"]:
        key = row["stationId"], row["pollutant"]
        if key[0] in quarantined:
            if findings is not None and findings[key] != finding("quarantined"):
                raise ValueError("Quarantined stations cannot be collected.")
            row.update(
                status="degraded",
                reasons=["quarantined"],
                degradedSince=row["degradedSince"] or now,
            )
            if key in accepted:
                exclusions[key] = "quarantined"
            continue
        if findings is None:
            if row["reasons"] == ["quarantined"]:
                row["reasons"] = ["notInspected"]
            continue
        result = findings[key]
        streams = approved[key]
        checked_finding(result, key, streams, now)
        outcome, candidate = result["outcome"], result["candidate"]
        preferred = max(
            streams, key=lambda s: importer.parse_series_url(s["sourceUrl"])[2]
        )
        if (preferred["sourceUrl"] not in urls) != (outcome == "missing"):
            raise ValueError("Channel outcome contradicts preferred-stream discovery.")
        latest = row["latest"]
        if result["withdrawal"] is not None:
            if latest is None:
                raise ValueError("Withdrawal requires a previously accepted reading.")
            interval = [latest[k] for k in VERSION[:3]]
            row["withdrawals"] = [
                w for w in row["withdrawals"] if [w[k] for k in VERSION[:3]] != interval
            ] + [
                {
                    **dict(zip(VERSION[:3], interval, strict=True)),
                    **result["withdrawal"],
                    "confirmedAt": now,
                }
            ]
            retire_withdrawals(row)
            latest = None
            if key in accepted:
                exclusions[key] = "withdrawn"
        floor = candidate and row["retiredBefore"].get(candidate["sourceUrl"])
        retired = bool(floor) and importer.parse_timestamp(
            candidate["observedTo"]
        ) <= importer.parse_timestamp(floor)
        if retired:
            candidate = None
        confirmed = latest is not None and result["latestReceipt"] is not None
        if confirmed:
            latest = {**latest, **result["latestReceipt"], "confirmedAt": now}
        normal_latest = latest
        if candidate is not None:
            published[key] = candidate
            normal_latest = latest_from(candidate, result["receipt"], now)
            row["hasObserved"] = True
        elif latest is not None and key in accepted:
            if (
                (outcome == "missing" or result["transient"])
                and latest["confirmedAt"] is not None
                and moment - importer.parse_timestamp(latest["confirmedAt"]) < FALLBACK
            ):
                published[key] = {
                    **accepted[key],
                    **{
                        field: stations[key[0]][field]
                        for field in ("stationName", "latitude", "longitude")
                    },
                }
                retained.append(key)
            else:
                omitted.append(key)
        proposals[key] = normal_latest, latest
        reasons = list(result["rejections"])
        if retired:
            reasons.append("retiredInterval")
        if any(stream["sourceUrl"] in missing for stream in streams):
            reasons.append("missingRegisteredStream")
        if outcome == "failed":
            reasons.append("sourceFailure" if result["transient"] else "invalidSource")
        if outcome == "empty" and row["hasObserved"]:
            reasons.append(
                "observationWithdrawn"
                if latest is None and row["withdrawals"]
                else "unconfirmedObservationLoss"
            )
        if reasons:
            row["degradedSince"] = row["degradedSince"] or now
            row["status"] = "degraded"
        else:
            row["degradedSince"] = None
            row["status"] = "available" if outcome == "available" else "healthy-empty"
        row["reasons"] = sorted(reasons)
    limits = budget(state)
    blocked_by = (
        "globalDiscoveryFailure"
        if discovery is None
        else "budgetExceeded"
        if not limits["withinBudget"]
        else None
        if published
        else "emptyNormalBatch"
    )
    decision = (
        "normal" if blocked_by is None else "corrective" if exclusions else "blocked"
    )
    for row in state["channels"]:
        key = row["stationId"], row["pollutant"]
        if key in proposals:
            row["latest"] = proposals[key][decision != "normal"]
    body = snapshot_body
    if decision != "blocked":
        rows = (
            [published[key] for key in sorted(published)]
            if decision == "normal"
            else [row for key, row in accepted.items() if key not in exclusions]
        )
        document = {
            "observations": rows,
            "importSummary": {
                "attempted": len(approved),
                "imported": len(rows),
                "skipped": len(approved) - len(rows),
                "failed": 0,
            },
            "publication": {
                "schemaVersion": 1,
                "mode": decision,
                "generation": state["generation"],
                "generatedAt": now,
                "degradedChannels": limits["degradedChannels"],
                "degradedStations": limits["degradedStations"],
            },
        }
        importer.validate_document(document)
        body = encode_snapshot(document)
    state["acceptedSnapshotSha256"] = digest(body) if body is not None else None
    validate_state(state, catalog, inventory, manifest_digest, body)
    normal = decision == "normal"
    return (
        state,
        body,
        {
            "decision": decision,
            "blockedBy": blocked_by,
            "exclusions": [
                {"stationId": key[0], "pollutant": key[1], "reason": exclusions[key]}
                for key in sorted(exclusions)
            ],
            "retainedFallbacks": [list(key) for key in sorted(retained)]
            if normal
            else [],
            "omittedReadings": [list(key) for key in sorted(omitted)] if normal else [],
        },
    )


def report_for(state, discovery, findings, publication):
    return {
        "schemaVersion": 2,
        "mode": MODE,
        "stateSha256": digest(builder.encode(state)),
        **publication,
        "budget": budget(state),
        "discovery": discovery,
        "outcomes": {
            kind: sum(value["outcome"] == kind for value in (findings or {}).values())
            for kind in sorted(OUTCOMES)
        },
    }


def read_run(path, catalog, inventory, manifest_digest):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Previous run must be a complete local directory.")
    names = {entry.name for entry in path.iterdir()}
    required = {"state.json", "collection-report.json"}
    if names not in (required, required | {"observations.json"}):
        raise ValueError("Previous run contains missing or unexpected files.")
    bodies = {}
    for name in names:
        source = path / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Previous run files must be regular files.")
        bodies[name] = catalog_tools.read_bounded(source)
    state = json.loads(
        bodies["state.json"], object_pairs_hook=catalog_tools.unique_object
    )
    report = json.loads(
        bodies["collection-report.json"], object_pairs_hook=catalog_tools.unique_object
    )
    snapshot = bodies.get("observations.json")
    approved = validate_state(state, catalog, inventory, manifest_digest, snapshot)
    catalog_tools.fields(
        report,
        (
            "schemaVersion",
            "mode",
            "stateSha256",
            "decision",
            "blockedBy",
            "exclusions",
            "retainedFallbacks",
            "omittedReadings",
            "budget",
            "discovery",
            "outcomes",
        ),
    )
    discovery = report["discovery"]
    if discovery is not None:
        if not isinstance(discovery, dict):
            raise ValueError("Invalid previous discovery report.")
        expected = catalog_tools.reconcile_discovery(
            discovery["matchedStreams"] + discovery["unregisteredStreams"],
            catalog,
            inventory,
        )
        if discovery != expected:
            raise ValueError("Previous discovery report is inconsistent.")
    catalog_tools.fields(report["outcomes"], OUTCOMES)
    counts = report["outcomes"]
    if any(type(count) is not int or count < 0 for count in counts.values()) or sum(
        counts.values()
    ) != (len(approved) if discovery is not None else 0):
        raise ValueError("Invalid previous channel outcome counts.")
    limits = budget(state)
    catalog_tools.fields(
        report["budget"], ("degradedChannels", "degradedStations", "withinBudget")
    )
    if (
        any(
            type(report["budget"][key]) is not int
            for key in ("degradedChannels", "degradedStations")
        )
        or type(report["budget"]["withinBudget"]) is not bool
    ):
        raise ValueError("Invalid previous degradation budget types.")
    accepted = snapshot_rows(snapshot, approved)
    exclusions = report["exclusions"]
    if not isinstance(exclusions, list):
        raise TypeError("Invalid previous exclusions.")
    for row in exclusions:
        catalog_tools.fields(row, ("stationId", "pollutant", "reason"))
        key = row["stationId"], row["pollutant"]
        if key not in approved or row["reason"] not in ("withdrawn", "quarantined"):
            raise ValueError("Invalid previous exclusion.")
        if key in accepted and row["reason"] == "quarantined":
            raise ValueError("Excluded reading remains in the accepted snapshot.")
    decision, blocked_by = report["decision"], report["blockedBy"]
    for name, kept in (("retainedFallbacks", True), ("omittedReadings", False)):
        if not isinstance(report[name], list) or any(
            not isinstance(key, list)
            or tuple(key) not in approved
            or decision != "normal"
            or (tuple(key) in accepted) != kept
            for key in report[name]
        ):
            raise ValueError("Invalid previous reading dispositions.")
    publication = (
        json.loads(snapshot).get("publication") if snapshot is not None else None
    )
    if decision in ("normal", "corrective") and publication != {
        "schemaVersion": 1,
        "mode": decision,
        "generation": state["generation"],
        "generatedAt": state["generatedAt"],
        "degradedChannels": limits["degradedChannels"],
        "degradedStations": limits["degradedStations"],
    }:
        raise ValueError("Published snapshot does not match its run report.")
    if (
        type(report["schemaVersion"]) is not int
        or report["schemaVersion"] != 2
        or report["mode"] != MODE
        or state["generation"] < 1
        or report["stateSha256"] != digest(bodies["state.json"])
        or decision not in DECISIONS
        or (decision == "normal") != (blocked_by is None)
        or (blocked_by is not None and blocked_by not in BLOCKERS)
        or (decision == "corrective" and not exclusions)
        or (decision == "normal" and (not limits["withinBudget"] or not accepted))
        or (blocked_by == "budgetExceeded") != (not limits["withinBudget"])
        and discovery is not None
        or report["budget"] != limits
        or (discovery is None) != (state["runIncident"] == "globalDiscoveryFailure")
        or (discovery is None) != (blocked_by == "globalDiscoveryFailure")
    ):
        raise ValueError("Previous run report does not match its state.")
    return state, snapshot, digest(bodies["collection-report.json"])


def write_run(output, state, report, snapshot_body):
    if os.path.lexists(output) or not output.parent.is_dir():
        raise ValueError("Choose a new output directory under an existing parent.")
    documents = {
        "state.json": builder.encode(state),
        "collection-report.json": builder.encode(report),
    }
    if snapshot_body is not None:
        documents["observations.json"] = snapshot_body
    if any(len(body) > catalog_tools.MAX_DOCUMENT_BYTES for body in documents.values()):
        raise ValueError("Degradation-state bundle exceeds the byte limit.")
    reserved = False
    exposed = False
    with tempfile.TemporaryDirectory(prefix=".eea-state-", dir=output.parent) as temp:
        staging = Path(temp)
        try:
            for name, body in documents.items():
                with (staging / name).open("xb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
            sync_directory(staging)
            output.mkdir()
            reserved = True
            os.rename(staging, output)
            exposed = True
            sync_directory(output.parent)
        except OSError:
            if exposed:
                for name in documents:
                    (output / name).unlink()
            if reserved:
                output.rmdir()
            raise


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
