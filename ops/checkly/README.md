# Atlas Checkly monitoring

Managed as standalone Checkly checks through the official MCP server. No Checkly CLI deployment is configured.

- `Atlas Refresh` (`4846df0d-497f-4008-8b3a-c31754cda413`): hourly POST to `refresh-eea.yml/dispatches`, `main`, GitHub API version `2026-03-10`, expected status 200. Authorization references the account secret `ATLAS_GITHUB_TOKEN`.
- `Atlas served data` (`edbbcb9b-a4a3-4dae-a613-5b5ea83dd42e`): public JSON every 15 minutes, maximum ingestion age strictly under two hours and at least one measurement strictly under six hours. Checks import summary, timestamps, observation identity and duplicate station/pollutant pairs; canonical source/schema validation remains in the existing Python publication pipeline.
- Both checks use Frankfurt and London in round-robin mode, no retries, and the existing owner email channel for failure/recovery alerts. TLS verification stays enabled.
- Baseline usage: 3,720 check runs per 31-day month. Do not add parallel dispatches or retries to the POST check: a lost response can follow an accepted dispatch.

`served-data.check.js` is the exact inline monitor source. `served-data.json` is a non-secret configuration reference; `scriptFile` is a local reference, not a Checkly API field. Changes here must be applied separately through MCP and verified against the remote configuration.

GitHub schedules remain enabled as fallback until external scheduled execution and notification delivery are verified. Test-session runs verify the configuration but do not prove recurring scheduler execution. Checkly recording a successful email send does not prove inbox receipt.

The account is on a 14-day Team trial, with automatic Hobby afterward. These checks deliberately use the free-plan schedule, locations, and email channel. The private Checkly user API key and local Codex authentication helper are outside this repository.

## Verification on 2026-10-10

- Checkly API test session `01a12615-696d-76ed-b003-901e7382f8cf`: passed; one GitHub workflow dispatch accepted.
- Checkly served-data test session `01a12612-5921-70d4-a8da-bf11620d0ffb`: passed from Frankfurt against fresh public JSON.
- Earlier owner-triggered refresh run `38056356825`: collect and publish succeeded; Pages deployment `38057070579` succeeded; cache-busted public JSON had latest ingestion `2026-10-10T13:46:48.233723+00:00`.
- A temporary scheduled check failed then recovered. Checkly recorded `ALERT_FAILURE` at `13:50:22Z` and `ALERT_RECOVERY` at `13:51:42Z`, with `SUCCESS` email-send records for both. Inbox receipt remains owner-confirmed evidence. The temporary check was deactivated then deleted with explicit owner confirmation.
- Regression check covers 19 freshness/malformed-snapshot cases. All 32 Node tests and ESLint passed.
- Both production checks were independently read back: activated, expected frequencies, Frankfurt/London round-robin, no retries, owner email subscription active, and deployed monitor script identical to the source here.
- First recurring production dispatch, its complete downstream publication, and steady schedule cadence still need observation before retiring GitHub fallback schedules.

This source and the earlier Python monitor/test changes are published together through the protected code-release gate. Remote Checkly configuration is managed separately through MCP.
