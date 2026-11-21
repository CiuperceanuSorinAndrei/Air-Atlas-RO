import argparse
import json
import os
import tempfile
from pathlib import Path

from scripts import eea_catalog as catalog_tools
from scripts import eea_registry as registry
from scripts import eea_resilience as resilience
from scripts import import_eea as importer


def snapshot_order(body):
    document = json.loads(body, object_pairs_hook=catalog_tools.unique_object)
    importer.validate_document(document)
    publication = document.get("publication")
    if publication is not None:
        return importer.parse_timestamp(publication["generatedAt"])
    return max(
        importer.parse_timestamp(row["ingestedAt"]) for row in document["observations"]
    )


def verify_run(manifest_path, run, previous_run=None):
    catalog, inventory, digest = registry.load_registry(manifest_path)
    state, snapshot, _ = resilience.read_run(run, catalog, inventory, digest)
    if previous_run is None:
        if state["generation"] != 1:
            raise ValueError("A run without a predecessor must start a new lineage.")
    else:
        _, _, previous_hash = resilience.read_run(
            previous_run, catalog, inventory, digest
        )
        if state["previousRunSha256"] != previous_hash:
            raise ValueError("Run does not continue the persisted lineage.")
    return snapshot


def export_snapshot(manifest_path, approval_path, run, target):
    catalog, inventory, digest = registry.load_registry(manifest_path)
    approval, _ = registry.read_json(approval_path)
    catalog_tools.fields(
        approval, ("schemaVersion", "registryManifestSha256", "approvedAt", "basis")
    )
    importer.parse_timestamp(approval["approvedAt"])
    importer.require_text(approval["basis"], "Production approval basis")
    if (
        type(approval["schemaVersion"]) is not int
        or approval["schemaVersion"] != 1
        or approval["registryManifestSha256"] != digest
    ):
        raise ValueError("Public export requires approval of this exact registry.")
    _, snapshot, _ = resilience.read_run(run, catalog, inventory, digest)
    current = catalog_tools.read_bounded(target)
    if snapshot is None or snapshot == current:
        return "unchanged"
    if snapshot_order(current) > snapshot_order(snapshot):
        return "publicNewer"
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".observations-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    return "exported"


def main():
    parser = argparse.ArgumentParser(
        description="Verify a controlled run against the persisted lineage, or export the accepted snapshot of a persisted run."
    )
    parser.add_argument("--registry-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--previous-run", type=Path)
    parser.add_argument("--production-approval", type=Path)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    if (args.export is None) != (args.production_approval is None) or (
        args.export is not None and args.previous_run is not None
    ):
        parser.error(
            "Verify with an optional --previous-run, or export with --production-approval and --export."
        )
    try:
        if args.export is None:
            verify_run(args.registry_manifest, args.run, args.previous_run)
            result = "verified"
        else:
            result = export_snapshot(
                args.registry_manifest, args.production_approval, args.run, args.export
            )
        report, _ = registry.read_json(args.run / "collection-report.json")
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        parser.exit(1, f"Controlled run rejected: {str(error)[:512]}\n")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "blockedBy": report["blockedBy"],
                "budget": report["budget"],
                "result": result,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
