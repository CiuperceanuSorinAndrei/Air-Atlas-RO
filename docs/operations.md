# Operations and recovery

## Code release

Run `bash scripts/check.sh` on the intended commit. Publish that commit to a review branch so
GitHub can run `production-checks`, including clean-database migration tests. `main` requires that
successful check from GitHub Actions, linear history, resolved review conversations and prohibits
force pushes/deletion. Fast-forward the same verified SHA to `main` or merge a checked pull request.
Never manufacture a passing check for code that has not completed the gate.

The refresh workflow checks its exact candidate, including SQL migrations and freshness, then
uploads only the JSON. Its write job revalidates coverage/interval regression against the same
base SHA. A temporary branch makes the candidate SHA available to GitHub; the workflow records
its actual completed gate as `production-checks`, pushes fast-forward to protected `main` and
removes the temporary branch. Failure or a racing main change leaves the published snapshot intact.
The next scheduled/manual run starts from the new main; no force push or automatic rebase is used.

## Import failure or stale page

Inspect `Refresh EEA observations` and the subsequent Pages run separately. Code, data import,
publication and deployment are distinct outcomes. Check public `observations.json`, its maximum
`ingestedAt`, failed/skipped counts and measurement freshness. A successful build alone does not
prove fresh data. The frontend expires old readings even when updates fail.

Transient network/provider failures may recover on the next run. Schema, identity, unit, coordinate
conflict, lost pair or regressed interval errors require source review. Never relax a guard simply
to turn a workflow green. For a genuine retired/revoked series, save the previous snapshot, record
the official evidence and affected station/pollutant pairs, make a reviewed baseline change and
rerun every gate. Same-interval corrections need no baseline reset.

Workflow-failure emails are owner-reported enabled. Delivery is not independently demonstrated;
verify a controlled failure reaches the chosen inbox before claiming alert coverage. Do not rely
on email or GitHub's hourly schedule as a production freshness SLA.

## Rollback

Revert the faulty code commit on a review branch, run checks, then merge/push the verified revert.
Do not reset/force-push main. For a bad data snapshot, restore the last accepted JSON in a reviewed
commit, documenting why intentionally older data is being used. Deploy that checked commit and
verify the public JSON/assets. Old data may correctly disappear from the recent map.

Database migrations are applied separately from code deployment. Current tables are empty and
private. The repository migrations reproduce the foundation; CI tests rebuild it from zero.
Before storing real history, establish backup retention and restore drills, then use additive,
reviewed migrations. Never point the disposable CI database script at production. Any future
writer must update rows and its ETag/cursor in one transaction and leave both unchanged on failure.

## Security and provider limits

No database credentials or privileged keys belong in Vite/browser assets. The `atlas` schema has
RLS and no public policies or application-role grants. Satellite assets remain separate from
surface observations. Public display requires verified rights, storage permission and attribution.

The HTML CSP restricts scripts/connections to the same origin, images to local/OSM and blocks
objects/base injection. Inline styles are allowed for Leaflet positioning. GitHub Pages controls
HTTP headers; custom `X-Content-Type-Options` and CSP response headers require another host.
Review each dependency/action/image update through the same checks; pinned versions are not a
substitute for security updates. Review the Supabase database patch level after provider upgrades.
