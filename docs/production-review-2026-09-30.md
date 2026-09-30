# Production engineering review — 2026-09-30

## Scope and conclusion

Reviewed the complete implemented checkpoint, starting at `45d9259`: importer/history helpers,
all tracked frontend/data/config/test files, workflows, dependencies, private Supabase/PostGIS
foundation, public deployment and project plan. The owner explicitly delegated these repairs.
This is assisted implementation; the earlier owner-written milestone remains distinguishable.

The existing phase is a partial static NO2/PM10 demo plus an empty private database foundation.
The goal is a safe verified baseline for further development, not certification of an unfinished
national production service. No finite audit proves the permanent absence of bugs. No compromise
or corrupt current snapshot was found in the initial audit.

## Finding closure

| Initial finding | Repair and verification |
|---|---|
| F01 — incomplete data/publication contract | Python and browser validate finite numbers, coordinates, unit, identities, quality/status, exact hour/day interval, explicit offsets, chronology, summary and unique pairs. Physical-station metadata must agree. Regression tests preserve previous bytes after rejection. JSON forbids NaN/Infinity. |
| F02 — unsafe/indefinite metadata cache | Validate every entry's exact identity/name/finite geographic range. Expire after 24 hours, reject future cache timestamps, refetch legacy/malformed entries, write atomically and tolerate optional cache I/O failure. |
| F03 — series identity mismatch | Bind every raw/normalized station, pollutant and sequence to the exact official series URL. Validate every history row before assigning one station's metadata. |
| F04 — unsafe/unbounded downloads | Official HTTPS allowlist and same-host redirects; reject file/local/arbitrary/query/traversal series URLs. Bound bytes, rows, decoded size, schema/groups and Parquet Thrift metadata; socket timeout and elapsed-download deadline. Reject nested fields before decoding. Browser reads at most 2 MB. |
| F05 — absent CI/publication gates | One repeatable code/data/build gate; new push/PR CI plus clean-database SQL checks. Refresh and Pages run those gates. Actions/image pinned. Separate read collection/build from write publication/deployment. Protected-main integration is verified as part of publication below. |
| F06 — unreproducible database foundation | Capture all six tables, constraints, indexes, RLS and private grants in repository migrations matching live migration versions. Add four covering FK indexes, finite/positive-interval checks and public-rights constraints. Rebuild in scratch schema transaction and roll back; exercise live constraints after upgrade. |
| F07 — vulnerable development dependency | Update only transitive `brace-expansion` to a patched lockfile revision; npm reports zero vulnerabilities. No new runtime dependencies. |
| F08 — documentation/plan drift | Rewrite README around actual behavior; add this evidence record, Markdown roadmap and publication/recovery runbook. Preserve history persistence, new sources and scoring as planned work. Update canonical vault control/registry separately. |
| F09 — measurement interval regression | Reject older `observedTo` for previously published pairs; preserve coverage and old snapshot. Same-interval corrections allowed. Genuine revocation/retirement requires reviewed baseline change, not an automatic bypass. |
| F10 — incomplete public attribution | OSM copyright link in map; visible EEA source/CC BY 4.0, transformation and non-endorsement statement. Code MIT terms kept separate. |

## Additional live finding

A clean metadata cache exposed an existing fallback initialization defect: the viewer GET lacked
its client headers while the following POST already supplied them. The public application gateway
returned 403 for fallback stations; the coverage guard correctly kept the previous snapshot.
Adding the same viewer client headers to initialization is covered by a regression test and live
station verification. Persistent 4xx denial is still visible; only transient blob failures are retried.

## Additional hardening and checks

- Frontend fetches the emitted public JSON every minute, validates it before replacement and
  rejects a snapshot older than the last accepted import. The six-hour filter keeps expiring
  readings during an outage; data/tile failures are visible. Accessible marker names, cluster
  help and a text list provide keyboard/non-map access. Popup contrast is corrected for dark mode.
- HTML CSP restricts script/connect sources and third-party images; React escapes station text.
  OSM remains the sole tile endpoint. No privileged database credentials are included in frontend.
- JS is reduced from about 558 kB to about 382 kB by serving snapshot JSON separately. Historical
  collections are not added to the browser bundle. No framework/service architecture was added.
- Refresh commits only the JSON, uses an exact tested base and fails on a racing main update.
  Publication recovery is a new checked run; no force push or silent rebase.
- SQL fixtures exercise allowed private inserts, cross-source rejection, NaN/empty intervals,
  rights denial/verified allowance, RLS and actual anon/authenticated read denial. Fixtures roll back.
- Supabase was upgraded by the owner through the dashboard after pause/restore retained the old
  image. Current API release is `17.11.0.002`; SQL reports PostgreSQL `17.11`, PostGIS `3.3.7`.
  The six Atlas tables remain empty/private; no scratch schema remains. See
  [Supabase upgrade guidance](https://supabase.com/docs/guides/platform/upgrading).
- Security advisor reports only six informational `RLS enabled no policy` entries, consistent
  with deliberate private denial. Performance advisor reports seven unused indexes because
  tables are empty; all missing FK-index findings are resolved. Adding permissive policies or
  deleting required indexes to suppress INFO notices would weaken this checkpoint. References:
  [private RLS notice](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy),
  [unused index notice](https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index).
- The original audit scanned tracked code/current data and locally available Git history for
  common credential patterns; none found. The eight locked Python registry dependencies had no
  OSV findings. The dependency set is unchanged. These bounded checks are not proof that every
  possible credential format or vulnerability is detectable.

## Verification evidence

Local clean-install verification uses locked Python 3.13.7 and Node with native TypeScript support:
80 Python tests and 16 frontend tests pass; Ruff lint/format, ESLint, snapshot validation, npm audit,
production build and whitespace checks pass. Original 41 Python tests remain included. `actionlint`
1.7.12 validates all workflow definitions. The CI PostGIS image is pinned by a verified upstream
SHA-256 and rebuilds migrations in a new `template0` database.

Chrome production-preview review independently confirmed the real map tiles, station markers,
readable popup intervals/status/source, accessible text list and copyright/licence links in dark
mode. This does not replace the owner's product review or constitute a complete accessibility audit.

The final live import succeeded: 359 attempted, 351 imported, 8 without valid observations,
0 failed; 197 physical stations preserved. Latest ingestion: `2026-09-30T20:13:30.225306Z`.
At review 306 readings were within the six-hour window. The new snapshot preserves hour/day
aggregation, source record IDs and data capture. The independent GitHub refresh repeated the
351-observation result with zero failed series; publication evidence follows below.

## Remaining boundaries

- GitHub cron and public OSM tiles have no production availability/freshness guarantee. Failure
  emails are owner-reported enabled, with delivery still not independently demonstrated. A
  national production release requires measured targets, tested alerts and operational ownership.
- GitHub Pages controls HTTP response headers; a strict CSP response header and custom nosniff
  configuration require a suitable host. The current HTML CSP is tested in the static preview.
- Database writer, durable ETag sync, complete history, API/auth flows, scoring, new providers,
  satellite/model integration and backup restore drills after persistence are not implemented.
  Their requirements are explicit gates in the roadmap, not claims of completed functionality.
- Changed UI and a 390 × 844 responsive viewport were independently checked. The latest public
  deployment was also verified. This remains assisted review, not owner acceptance or a complete
  accessibility certification. The hourly snapshot can legitimately contain daily PM10.

## Publication evidence

- Source repair commit [`923c76d`](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/commit/923c76da1ebc0d7a806197d63eb650936bd2dbca) has the identical tested local tree.
- [Review CI 36771567868](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/actions/runs/36771567868) and [main CI 36771763270](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/actions/runs/36771763270) succeed, including a fresh PostGIS rebuild and rollback-only SQL fixtures.
- Main requires `production-checks` from GitHub Actions (app 15368), a current base, linear history
  and resolved conversations, including for admins. Force push and deletion are disabled.
- [Protected refresh 36771899595](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/actions/runs/36771899595) succeeds in both collection and publication jobs. It publishes only the validated JSON as
  [`e3becbd`](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/commit/e3becbd92fa4e7a19ccbd79124f8bc24602e5236), with an actual successful GitHub Actions check on that exact SHA.
- [Automatic Pages 36773204129](https://github.com/CiuperceanuSorinAndrei/Air-Atlas-RO/actions/runs/36773204129) succeeds for that data commit. Public HTML, JS/CSS and JSON return HTTP 200; public JSON
  is byte-identical to the checked repository snapshot, with ingestion `2026-09-30T20:30:33.866480+00:00`.
- Public Chrome review without profile extensions shows zero console messages and no CSP issue.
  Profile-only MetaMask content-script warnings and an eval-block notice disappear in this session;
  CSP remains restrictive. No unsafe-eval exception was introduced.
- The original local main is fast-forward synchronized. Repository README, roadmap and operations
  describe the implemented phase and subsequent gates. Vault controls are updated locally; unrelated
  vault work is preserved and not included in a repository commit.

All discovered defects in the implemented checkpoint are closed with the evidence above. The
remaining boundaries are future capabilities or explicitly documented operational limitations,
not unreported implemented-feature defects. Dependency-update proposals remain separate reviewed
changes; this checkpoint does not automatically adopt new major versions.
