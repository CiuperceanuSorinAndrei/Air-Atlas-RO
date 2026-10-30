# Local catalog/inventory checkpoint — 30 October 2026

Authorized scope: implementation, necessary tests and repository documentation for Geoapify
restriction verification and the first working catalog/inventory slice. The subsequent closure
also authorizes regression fixes, protected commit/PR/merge, release verification and vault archival.
No provider setting change or remote database write is part of this checkpoint.
Base local HEAD: `f3b83a13500d4b292fd7cd9213851205b9cb6a36`.

## Result and limits

- Authenticated Geoapify origin settings were read back. The three allowed origins returned 200;
  an unlisted origin returned 401; an Origin-less request returned 200. No credential was logged.
  Public UI search returned selectable approximate matches. Existing production/development key
  remains shared. See [address-search.md](address-search.md).
- [Catalog/inventory](station-catalog.md): strict separate schemas, exact native stream identity,
  XML delivery consistency, bounded offline discovery inspection, missing/unregistered reporting,
  and opt-in collection without live metadata/cache I/O for known catalog stations.
- Hard errors stop a channel's sequence fallback; only eligible-empty sequences allow older
  candidates. Numeric duplicate series identities are rejected before fetching.
- No real approved national registries were fabricated or activated. Controlled degradation,
  persistent incident accounting, corrective publication and history storage remain future work.

## Independent review and dispositions

AGY was used as read-only external reviewer on explicitly authorized source/docs/test snapshots;
credentials, local environment files, browser captures and the vault were excluded. Exact pack
manifests, prompts, responses and verification logs are retained locally under the ignored
`.cache/reviews/2026-10-30/` directory, not included in public source.

Plan review `25162b13-56e4-4f66-b68e-2df83fe673fa` inspected the existing importer and contract.
Accepted: hard-error fallback defect; default-call compatibility; full delivery provenance;
explicit streams; bounded parsing; fail-closed catalog membership. Inventory array order was
not made a second priority rule: existing descending numeric sequence remains authoritative.

Implementation review `59eb8457-2cd3-42b2-902e-84c6e9e6be57` inspected the actual source/tests/docs.
Accepted/fixed: duplicate numeric series checks; equal reporting-period offsets; regression
covering other-channel continuation; CLI review exits for geometry and unknown-only discovery.
Rejected the speculative offset allowlist: no inspected source establishes permitted official
timezone offsets, and this slice preserves exact evidence rather than inventing a universal
national time policy. The catalog checks annual boundaries,
microseconds, offset awareness and offset consistency. Official provenance still requires review.
Redundant bounded in-memory validation and richer CLI diagnostic wording remain optional, with
no demonstrated correctness failure; no additional architecture or dependency was added for them.

## Extended closure review

The broader pre-publication audit also inspected the reused XML, discovery, history and publication
boundaries. Fixed the catalog CLI's uncaught `RecursionError` on deeply nested JSON, with catalog
and inventory subprocess regressions. Aligned the live XML reporting-period gate with the catalog:
zero microseconds and equal begin/end offsets, preserving the source's actual offset without an
invented timezone allowlist. All three added XML cases first failed against the earlier parser,
then passed after repair while proving existing metadata-cache bytes survive rejected input.
The session adds 73 tests in total; broad runtime/CI evidence is recorded separately from AGY review.

## Verification boundary

The local Python suite passes 727 tests with 64 database tests skipped because no
disposable `ATLAS_TEST_DATABASE_DSN` is configured; schema/database integration was not newly
verified. All 35 frontend tests, Ruff check/format, ESLint, snapshot-contract check and production
build pass, including the repository's complete `bash scripts/check.sh` gate (exit 0) and
`npm audit` with zero vulnerabilities. The final reviewer response and matching source manifests
are retained with the local evidence. Local evidence is separate from remote CI and deployment.

The offline CLI also passed on a one-station candidate derived from the captured October 28
official Romanian XML: `RO0008R` / `EM-3`, header `D_ReportingHeader`, XML SHA-256
`714f08549e37ab0d2edafd8f3a3c678a4d880419170b2cd5e544b13110cd407d`, one matching NO2
stream, no missing or unregistered entries. This is a bounded replay of captured public evidence,
not a new national approval, live source-freshness check or active production catalog.

## Restart

Next implementation work is constructing and
reviewing real national catalog/inventory artifacts before changing the scheduled measurement job;
controlled degradation follows as a separate reviewed change. Do not reuse this session's scoped
implementation permission as permanent delegation.
