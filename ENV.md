# Environment configuration

Use `.env.example` as a list of variable names, with empty placeholders except the example port. The application does not automatically load `.env`: export variables in your shell or configure them in Render's environment settings. Leave optional variables unset to use defaults; do not export empty path values. Never commit real credentials.

| Variable | Required vs optional | Meaning / default |
| --- | --- | --- |
| `RESULTS_PREDICTOR_HOSTED` | Required as `1` for this hosted deployment | Enables hosted storage rules. `true` and `yes` also enable them; presence of Render's `RENDER` variable independently enables hosted mode. Unset locally by default. |
| `TURSO_DATABASE_URL` | Required when hosted | Remote URL for your Turso database. When both Turso credentials are set, remote storage is also used locally. |
| `TURSO_AUTH_TOKEN` | Required when hosted | Secret auth token for that database, with write access for scans/scoring. |
| `RESULTS_PREDICTOR_WRITE_TOKEN` | Required for the Blueprint deployment; optional for local use | Choose your own secret for write API requests. Send it in `X-Write-Token` or `Authorization: Bearer <token>`, or enter it in the UI token field. The application does not enforce its presence at startup; without it and with public reads enabled, writes are unauthenticated. |
| `RESULTS_PREDICTOR_PUBLIC_READ` | Optional | Unset or `1` allows public report/export/backup API reads. Exactly `0` requires the write token for these reads too. Configure a write token before setting `0`. HTML pages and `/health` remain public. |
| `PORT` | Optional | Server port; local default is `8765`, example is `10000`. Render supplies this variable; the Blueprint passes it through `--port`. |
| `RESULTS_PREDICTOR_DB` | Optional; local use | Local SQLite ledger path, default `data/results_predictor.sqlite3`. Does not replace Turso in hosted mode. |
| `RESULTS_PREDICTOR_CACHE` | Optional | Local provider cache SQLite file, default `cache/providers.sqlite3`. Cache is disposable and can be regenerated. |
| `RESULTS_PREDICTOR_REPORTS` | Optional | Local report/CSV directory, default `reports`. Hosted mode skips the local ledger audit CSV; other generated reports are ephemeral. |

## Create storage yourself

You must create your own Turso account, database and auth token. The Blueprint does not provision them. Example CLI commands only (run yourself after installing the Turso CLI):

```bash
turso auth login
turso db create results-predictor
turso db show results-predictor --url
turso db tokens create results-predictor
```

Copy the URL and token into Render's secret environment settings, alongside your own application write token. Do not paste secrets into tracked files. See the [Turso CLI reference](https://docs.turso.tech/cli/introduction).

With `RESULTS_PREDICTOR_HOSTED=1`, missing Turso credentials raise an error: there is **no silent local SQLite fallback**. Driver/connection failures also fail rather than switching storage. `/health` returns HTTP 503 with `db_ok: false` when the database is unavailable. The remote ledger uses the [turso_serverless HTTP driver](https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx), installed as a runtime dependency.

[Render Free has an ephemeral filesystem](https://render.com/docs/free). Local SQLite, cache and reports can be lost on restart, redeploy or spin-down; do not rely on their persistence. Predictions must persist in Turso. Cache loss is harmless to ledger durability, though providers may need to be fetched again.

## Deployment and restart persistence verification

1. Commit and git push the deployment files before creating the Render Blueprint. Connect the pushed repository and fill the three secret placeholders. Wait for the `/health` check to pass with `db_ok: true` and `storage: "turso"`.
2. Enter your write token in the UI and run an authenticated offline scan for `2026-11-09` through `2026-11-13`. Alternatively POST `/api/scan` with `X-Write-Token` and body `{"start":"2026-11-09","end":"2026-11-13","offline":true}`.
3. Read `/api/report` (include the same authentication if public reads are disabled). Record the total `rows` count and all `prediction_id` values; keep a copy of the response outside Render.
4. Restart the Render service, then verify again after allowing a Free service to spin down and waking it with a request. Allow startup time for the wake.
5. After each restart/wake, confirm `/health` returns HTTP 200, `db_ok: true` and `storage: "turso"`. Fetch `/api/report` again without running another scan. Verify the same prediction IDs and count remain. If other users added scans, verify every original ID remains and the count has not decreased.

This is an operator verification plan; offline unit tests alone do not prove persistence on the deployed service.
