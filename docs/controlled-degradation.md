# Controlled degradation — local opt-in run

`--controlled-degradation` is a private, opt-in importer path over the pinned
reviewed registries. Each run collects every approved channel, keeps the
fixed-inventory degradation budget across restarts, binds accepted readings to
verified source versions, applies 24-hour transport fallback, withdrawal and
station quarantine, and decides between a normal, a strictly corrective or a
blocked result. It implements the policies in the
[accepted contract](reliability-and-history-contract.md).

It writes only a new private bundle directory. The default importer and the
strict registry trial keep their existing behavior, and the scheduled refresh
uses this command only when the owner enables one of the opt-in modes in
[controlled activation](controlled-activation.md). Production approval of the
registry is a separate reviewed document.

## Invocation and restart

Initialize a new local lineage explicitly:

```sh
uv run --locked python -m scripts.import_eea \
  --registry-manifest data/eea/registries/v1/manifest.json \
  --controlled-degradation --initialize-state \
  --previous-snapshot src/data/observations.json \
  --output-directory /private/tmp/atlas-run-1
```

Continue from the latest complete local bundle:

```sh
uv run --locked python -m scripts.import_eea \
  --registry-manifest data/eea/registries/v1/manifest.json \
  --controlled-degradation \
  --previous-run /private/tmp/atlas-run-1 \
  --output-directory /private/tmp/atlas-run-2
```

Both commands download real Parquet files. `--discovery` pins membership to a
captured discovery response; it does not make collection offline. Automated
tests use synthetic, network-blocked fixtures. `--previous-snapshot` is
optional and initialization-only; it may be a legacy snapshot or the accepted
snapshot of an earlier controlled lineage, and its readings start unconfirmed. Supplying neither initialization nor a
previous run, both, or a missing/corrupt previous run fails; the code never
silently starts a new budget. Exit 0 means a complete bundle was saved,
including a blocked result; read `decision` in the printed summary or report.
CLI/input/I/O failure is nonzero and exposes no output directory.

## Bundle

- `state.json`: every approved channel with `hasObserved`, status, reasons,
  `degradedSince`, the `latest` accepted source version, retained
  `withdrawals` and per-stream `retiredBefore`; station `quarantine`; registry identity; predecessor hash.
- `collection-report.json`: `decision`, `blockedBy`, budget, discovery,
  outcome counts, `exclusions`, `retainedFallbacks`, `omittedReadings`.
- `observations.json`: the accepted snapshot after this run, when one exists.

The report hashes the exact state bytes, state hashes the accepted snapshot and
the next run references the preceding report hash. Strict schemas, duplicate-key
rejection, complete channel membership and bounded 8 MiB files are checked on
restart. Files and directories are fsynced before atomic exposure; write errors
remove this run's output and leave input bundles untouched. Existing outputs
and symlinks are refused. Hashes detect inconsistent files, not an operator
replacing all of them. One operator selects the latest complete bundle;
choosing an older predecessor or initializing again forks the lineage. There
is no shared production pointer or concurrent scheduler.

## Budget

A channel with several causes counts once. Up to **6 channels and 3 stations**
degraded is within budget; 7 channels or 4 stations exceeds it. Uninspected
channels start degraded. A never-observed, successfully decoded empty channel
is `healthy-empty`; a channel that once had a reading stays degraded while it
is empty, including after a withdrawal without replacement. A missing
registered stream degrades its channel even when the preferred stream provides
a reading. Omissions never imply retirement or shrink the inventory. Unknown
discovery streams are reported and never collected.

Reasons: `notInspected`, `missingRegisteredStream`, `sourceFailure` (transient
transport), `invalidSource` (schema, identity, unknown codes, non-retryable
HTTP), `unconfirmedObservationLoss`, `observationWithdrawn`,
`retiredInterval`, `unconfirmedRegression`, `staleSourceVersion`, `versionConflict`,
`quarantined`.

## Source confirmation and version order

Streams are tried from the inventory in descending sequence. Only a decoded
sequence with no eligible observation permits the next one; a missing or
failing preferred stream never falls back to a lower sequence.

`latest.confirmedAt` is set only when the accepted reading itself was matched
in a downloaded payload whose every row passed identity and validity-code
checks: same interval, same `ResultTime` and the same measurement record
(value, unit, validity, verification, aggregation, source record ID, data
capture). The receipt stores that payload's SHA-256 and ETag. An HTTP 200, a
successful decode, a different newer reading or an invalid payload do not
renew it. Readings bootstrapped from a legacy snapshot have
`confirmedAt: null` until verified this way; no time is derived from
measurement, ingestion or migration.

Order comes from the interval and the native `ResultTime`, never from arrival
time, ETag or hash:

- a later interval replaces the accepted reading;
- the same interval with a later `ResultTime` is a correction;
- the same interval and `ResultTime` with a different record is
  `versionConflict`; an earlier `ResultTime` is `staleSourceVersion`;
- an earlier interval is `unconfirmedRegression` unless the same verified
  payload marks the accepted interval with validity `-1`. Validity `-99` or an
  absent row is not a withdrawal. A lower sequence cannot introduce such a
  regression either.

A rejected candidate leaves the accepted version in state unconfirmed.

## Withdrawal

Validity `-1` on the accepted interval, reported no earlier than the accepted
version, is a confirmed withdrawal. The reading is removed and the withdrawn
interval, its `ResultTime` and the payload receipt are kept in `withdrawals`.
A later payload presenting that interval as valid is accepted only with a
later `ResultTime`; equal is `versionConflict`, earlier is
`staleSourceVersion`. Withdrawal records belong to their native stream: a
lower sequence reached after a decoded-empty preferred stream is separate
evidence, not a version of the withdrawn record.

Each channel keeps its **8 most recent withdrawn intervals** (by interval end,
counted across all its streams). When a ninth is recorded, the oldest record is
retired: its interval end becomes `retiredBefore` for that record's native
stream, and no reading of that stream ending at or before that time is
accepted again, whatever its `ResultTime`. Such a reading is rejected as
`retiredInterval`; like any other rejected version it does not permit a lower
sequence, so the channel stays degraded until that stream reports a later
interval. A floor is never compared with readings of another stream, although
withdrawals on one stream of a channel can retire the older record of another.
A channel is never frozen by its withdrawal count, and withdrawal evidence
stays bounded: all 717 approved channels at the window limit fit inside the
8 MiB state limit. The cost is deliberate: once eight more recent withdrawals
exist, an older withdrawn interval can no longer be re-validated, and the
stream cannot step back to a still-valid reading at or before it. State files
use `schemaVersion: 3`; bundles written by the earlier schema 2 are rejected
and need a new initialization.

## Transport fallback

When the preferred stream is absent from discovery or fails transiently
(timeouts, connection errors, HTTP 429/500/502/503/504 after the existing
retries), a normal publication keeps the previously accepted row while
strictly less than 24 hours have passed since `latest.confirmedAt`. Its
observation and ingestion timestamps are unchanged; only station name and
coordinates follow the approved catalog. At 24 hours the reading is omitted
and listed in `omittedReadings`; the channel stays degraded and its version
stays in state. Unconfirmed legacy readings, hard failures, conflicts and
regressions get no fallback window.

## Station quarantine

`--authority-evidence FILE` supplies reviewed findings bound to the exact
registry manifest:

```json
{
  "schemaVersion": 1,
  "registryManifestSha256": "…",
  "findings": [
    {
      "stationId": "RO0008R",
      "kind": "geometry",
      "status": "quarantined",
      "sourceUrl": "https://cdr.eionet.europa.eu/ro/eu/aqd/d/env…/….xml",
      "reportingYear": 2026,
      "xmlSha256": "…",
      "headerId": "…",
      "reviewedAt": "2026-11-09T00:00:00+00:00"
    }
  ]
}
```

`kind` is `identity` or `geometry`. The evidence must be another official
delivery than the one the catalog row came from; a later year or different
hash alone decides nothing, the reviewed finding does. Renames, XML omissions
and activity ends are not quarantine. The command makes no live metadata
request and does not verify the XML itself.

A quarantined station is not downloaded, has no reading in any published
snapshot, and all its approved channels count against the budget. Its
versions, withdrawals and the evidence fields stay in state. Quarantine
persists across runs without the file and ends only with a finding whose
`status` is `resolved`.

## Decision and publication

- **normal**: discovery valid, budget respected and at least one reading.
  The snapshot holds the newly verified readings plus fallback rows, without
  withdrawn, quarantined or omitted readings.
- **corrective**: normal is blocked (budget exceeded, global discovery
  failure or an empty normal batch) and the accepted snapshot contains
  confirmed withdrawals or quarantined stations. The snapshot is exactly the
  previous one minus those rows, in the same order and otherwise unchanged.
  Newly collected readings never enter; they affect only availability state.
  It may be empty.
- **blocked**: no justified exclusion. The accepted snapshot bytes are kept
  unchanged. Fallback expiry alone never removes a row here.

Every result, including blocked, persists the degradation state, so losses
accumulate and the incident is not reset.

Normal and corrective snapshots carry
`publication: {schemaVersion, mode, generation, generatedAt, degradedChannels, degradedStations}`.
`importSummary` describes channel disposition: `attempted` is the approved
channel count, `imported` the published rows, `skipped` the rest, `failed` 0;
real outcomes are in the report. The Python and frontend validators accept the
envelope, require `generatedAt` not earlier than any ingestion time and allow
an empty `observations` array only for `mode: corrective`. The frontend orders
snapshots by `generatedAt` when present, so a correction replaces a displayed
legacy snapshot; a legacy snapshot cannot replace a later controlled one in
an open session. `check_served_data.py` and the Checkly script source measure
the two-hour limit from `generatedAt` for controlled snapshots and report an
empty correction as degraded. The legacy writer rejects envelopes and empty
batches. The deployed Checkly check still runs the earlier script until it is
updated separately.

## Not covered

The command itself schedules nothing and writes only private bundles.
[Controlled activation](controlled-activation.md) describes the opt-in
workflow modes, the persisted state branch, monitoring and rollback; that path
is implemented but not active. No history API/UI or storage redesign. Dynamic
metadata cache policy is untouched because catalog stations make no live
metadata calls. The source's semantics for `ResultTime` on invalidation are
not documented by the provider; withdrawals are honored when not earlier than
the accepted version.
