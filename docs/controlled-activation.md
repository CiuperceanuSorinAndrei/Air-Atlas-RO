# Controlled refresh — activation, operation and rollback

Status, 21 November 2026: implemented and tested locally; **not active**.
The scheduled refresh keeps publishing with the default importer until the
owner sets the repository variable described below. No remote run of the
controlled jobs, state branch or updated monitor has happened yet.

The policies are those of the [accepted contract](reliability-and-history-contract.md);
the run itself is described in [controlled degradation](controlled-degradation.md).

## Modes

`refresh-eea.yml` reads the repository variable `ATLAS_CONTROLLED_REFRESH`.
A repository variable is not part of the approved code fingerprint, so
changing it needs no release and is the single switch for rollback.

| Value | Collection and publication |
| --- | --- |
| unset or anything else | Default importer only. Behavior identical to the previous workflow. |
| `shadow` | Default importer publishes as before. In parallel jobs the controlled run is collected and its state persisted; nothing from it reaches `main`. Failures of the controlled jobs do not fail the workflow or delay the data deployment. EEA downloads are doubled while this mode is on. |
| `on` | Only the controlled run is collected. Its accepted snapshot is mirrored to `src/data/observations.json` through the same protected publication step. The default importer jobs are skipped. |

## Persisted state

Each controlled run is stored as one commit on the branch `refresh-state`,
directory `run/` (`state.json`, `collection-report.json` and, when a snapshot
is accepted, `observations.json`). The branch history is the lineage. State is
never committed to `main`: the approved-site fingerprint covers every tracked
file except the snapshot, and publication may change only the snapshot.

`scripts/persist_refresh_state.sh` runs in the write job:

1. It reads the branch head. A missing branch is accepted only for an explicit
   initialization; an unreadable remote always fails.
2. `scripts.publish_controlled_run` validates the new bundle offline against
   the pinned registry and requires its `previousRunSha256` to equal the hash
   of the report at the branch head. An initialization must be generation 1.
3. The bundle is committed with the previous head as parent and pushed without
   force. A rejected push fails the job and changes nothing.
4. Only in mode `on`, and only after the state is persisted, the accepted
   snapshot of that run is exported into the working tree (guards below) and
   `scripts/publish_snapshot.sh` publishes it to `main` exactly as the default
   path does.

The state branch is the source of truth; `main` is a mirror of its accepted
snapshot. Consequences for every interruption:

| Event | Result |
| --- | --- |
| Collection fails or is cancelled | Nothing persisted; the next run continues from the same head. |
| Verification fails (corrupt bundle, wrong predecessor) | Nothing persisted or published; job fails. |
| State push rejected | Nothing published; job fails; the next run collects again from the real head. |
| State pushed, export refused (approval missing or not for this registry) | Job fails; state keeps advancing on later runs; nothing is published until the approval is fixed. |
| State pushed, `main` advanced meanwhile | Publication step exits with its existing notice. The next run exports the then-accepted snapshot, which includes this one. |
| State pushed, final push to `main` fails | Job fails; the next run mirrors as above. |
| `blocked` decision | State advances; accepted snapshot bytes are unchanged, so `main` receives no commit. |
| `corrective` decision | State advances; the corrected snapshot, possibly empty, is published. |

Export guards: a `production-approval.json` naming the exact registry
manifest SHA-256 must exist, otherwise the export fails. The public snapshot
is replaced only when its order time (`publication.generatedAt`, or the newest
`ingestedAt` of a legacy snapshot) is not later than that of the accepted
snapshot; otherwise the command reports `publicNewer` and leaves it alone. This
happens when a `blocked` run follows a switch from the default importer: the
lineage's accepted snapshot is older than the last legacy publication. The
next normal or corrective run is newer and is published.

## Monitoring

`scripts/check_served_data.py` and `ops/checkly/served-data.check.js` accept
both snapshot forms. For a controlled snapshot the two-hour limit applies to
`publication.generatedAt`, because corrective publications and retained
fallback rows do not advance any `ingestedAt`; legacy snapshots keep the
newest `ingestedAt`. At least one reading younger than six hours is still
required. Effects:

- `blocked`: the public snapshot stops changing and the monitor alerts after
  two hours. The cause is in `run/collection-report.json` on `refresh-state`
  and in the workflow summary.
- `corrective`: publication stays recent; the monitor alerts only if no
  recent reading remains, including an empty correction.
- Mode `on` commits a snapshot on every normal run, as today.

The Checkly check is not deployed from Git. The updated script must be applied
through the Checkly MCP server and read back; it is compatible with the current
legacy snapshots, so it can be applied before activation.

## Activation procedure

Each step needs the owner's explicit decision.

1. Release this code. Confirm no ruleset blocks a bot push to a new
   `refresh-state` branch.
2. Apply and read back the Checkly script.
3. Set `ATLAS_CONTROLLED_REFRESH=shadow`, then dispatch the workflow once with
   `initialize-state: true`. The lineage starts from the public snapshot: all
   readings unconfirmed and every channel `notInspected`, so seven or more
   degraded channels in that first run give `blocked`. Later runs recover.
4. Observe at least 72 consecutive hourly runs (three 24-hour fallback
   windows). Acceptance: one state commit per run with an unbroken parent
   chain; every `blocked` or `corrective` decision explained; the budget of 6
   channels / 3 stations never exceeded in normal provider operation, or the
   exceptions understood; differences from the default snapshot explained;
   withdrawals per channel far below the window of 8; collection inside the
   30-minute limit.
5. Repeat the catalog review (`capture_eea_candidates`, then
   `build_eea_candidates` with `--previous-catalog` and `--previous-inventory`)
   and resolve its `baselineDiff`.
6. Add `data/eea/registries/v1/production-approval.json` through a reviewed
   pull request:

   ```json
   {
     "schemaVersion": 1,
     "registryManifestSha256": "<sha256 of v1/manifest.json>",
     "approvedAt": "<UTC timestamp>",
     "basis": "<what was reviewed>"
   }
   ```

   The technical review inside the registry stays `local-trial`; this separate
   document is the production approval and does not change the manifest hash,
   so the shadow lineage continues.
7. Set `ATLAS_CONTROLLED_REFRESH=on` shortly after an hourly run finished.
   Verify the next run, the published `publication` envelope, the Pages data
   deployment, the served bytes and the monitor.

## Rollback and re-activation

Set `ATLAS_CONTROLLED_REFRESH` to `shadow` or delete it. The next run uses the
default importer, which accepts a controlled snapshot as its previous
snapshot and publishes one without the envelope; open browser sessions accept
it because its newest ingestion is later than the last `generatedAt`. The
default importer applies its usual missing-pair and interval-regression guards
against that previous snapshot; if it refuses, follow the import-failure
procedure in [operations](operations.md) rather than relaxing a guard.

Returning to `on` continues the persisted lineage; the first normal run is
published and `blocked` runs before it leave the newer legacy snapshot in
place. In `shadow` the lineage keeps advancing, so prefer `shadow` to deleting
the variable. A new initialization (dispatch with `initialize-state: true`) is
needed only for a new registry manifest or a deliberately discarded lineage;
it keeps the branch history and resets the degradation budget, confirmations
and withdrawal evidence, so record why.

## Catalog review

Weekly and on demand, with the capture/build commands in
[station catalog](station-catalog.md). New stations, renames, relocations and
corrections are reviewed pull requests that create a new registry version; a
new manifest needs a new approval document and a new lineage. There is no
automatic expiry of the approved catalog.

## Limits

One lineage, one scheduler: the workflow concurrency group is the only
protection against concurrent runs. Hashes detect inconsistent bundles, not a
maintainer rewriting the state branch. The state branch grows by about 1 MiB
of history per run before Git compression; prune it only with a documented
new initialization. Nothing here has run on GitHub yet.
