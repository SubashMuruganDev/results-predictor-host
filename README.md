---
title: Results Radar
emoji: 📊
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# results_predictor

A browser-first results-season workspace for NSE F&O equities. It discovers companies with financial-results meetings, runs the existing v2 forecast beside the old seasonal expectation E1, and keeps predictions in SQLite for later scoring. Forecasts compare a company with its own history, not analyst consensus. They are not trade recommendations.

Install from this checkout with Python 3.10 or newer:

```bash
python3 -m pip install -e .
python3 -m pip install pytest  # tests only
```

The `results-predictor` console command and `python3 -m results_predictor` are equivalent. Runtime dependencies are pandas, requests and turso_serverless. The unchanged legacy model files use numpy, which pandas installs. Local use needs no credentials or tokens; hosted use requires the configuration below. No rich, lxml or matplotlib is required. Reports use plain terminal tables and CSV; PNG generation is not implemented.

## Web UI (start here)

```bash
python3 -m results_predictor serve
```

Open **http://127.0.0.1:8765**. No frontend build, app.py, Flask, credentials or API keys are needed. The server uses Python's stdlib `http.server`. Use `--port 9000` for another port; `PORT` overrides the default port. Local binding defaults to 127.0.0.1.

The **Discover** page has inclusive date pickers, an offline/sample checkbox (on by default), optional options analysis, a force-refresh checkbox, and a Scan button. The bundled sample calendar covers **2026-11-09 through 2026-11-13**. A responsive, horizontally scrollable table shows color-coded v2 calls, lean/confidence, old E1 and proxy calls, options context, reasons and CE/PE analysis. Loading indicators, error messages and a coverage-gap panel show what is available. Download the current scan as CSV.

The **Report card** page reads SQLite and shows original predictions alongside filed actuals, nullable price reactions, status, run IDs and timestamps. Use **Fetch actuals & score** after results appear, selecting sample or live predictions explicitly. Monthly hit rates are grouped by metric and model; NO CALLs and unavailable actuals do not enter the hit-rate denominator. Download the complete ledger as CSV. Every page carries the analysis-only banner. Sample actuals are never substituted for missing real filings.

JSON endpoints (same-origin, no authentication):

- `POST /api/scan`: `{"start":"2026-11-09","end":"2026-11-13","offline":true,"options":true,"refresh":false}` returns `rows`, `events`, `offline`, and `coverage_gaps`. Each scan creates a new immutable run.
- `GET /api/report`: returns `rows` and monthly `scoreboard` from SQLite.
- `POST /api/score`: `{"offline":true}` reconciles pending predictions and returns the report plus coverage gaps.
- `GET /api/export`: downloads ledger rows as CSV.

The CLI is a thin entry point over the scanner, scoring and UI services. Live mode remains best effort: restricted NSE access is reported explicitly; turn offline mode back on to use the sample. The hosted UI starts in sample mode too, so a blocked network does not prevent use.

## Immutable prediction ledger and actuals

Every forecast from the UI, season CLI or legacy symbol/file CLI is inserted into SQLite **`predictions_log`** and appended to **`reports/predictions_log.csv`**. Rows include prediction ID, run UUID, creation timestamp with the IST `+05:30` offset, symbol/date/metric, v2 call/lean/confidence, old E1/proxy call, options context, frozen thresholds, input timestamps and full payload. An additional database-path CSV column disambiguates IDs from multiple database files. Repeated scans append new runs; they never overwrite earlier forecasts. Existing pre-ledger predictions are imported without re-running their models; missing historical fields stay unavailable.

SQLite triggers reject UPDATE and DELETE on `predictions_log`, `actuals`, and `scores`. Mutable workflow state remains in the existing `predictions` table and `transitions` audit trail. `score --auto` appends filing observations and scores referencing the original prediction ID; re-scoring a completed row leaves its observation unchanged. Actuals retain filing provenance and a nullable `reaction_pct`: a reaction explicitly supplied by the filing adapter is preserved; the current exchange adapter does not fetch separate historical prices, so these usually remain null. Reactions are never invented or used as earnings labels.

```bash
python3 -m results_predictor score --auto --offline
python3 -m results_predictor scoreboard
```

The old call is a **transparent proxy**, not the unavailable pickled classifier: compare the latest same-quarter YoY change with the median of the last four changes (at least three required). Sales uses percentage changes; margin and PAT use absolute changes. A positive difference is BEAT, negative is MISS, equality or insufficient history is NO CALL. E1 and all v2 model calculations are preserved.

## Persistent cache

Provider data survives process restarts. The default cache is `./cache/providers.sqlite3`; set `RESULTS_PREDICTOR_CACHE` to relocate it. Universe snapshots live in the selected application SQLite database. Offline and online sources use separate cache keys; fixture directories also remain separate.

| Data | Freshness / retention |
| --- | --- |
| F&O universe | 24 hours |
| Primary and secondary calendars | 6 hours, keyed by inclusive date range |
| Fundamentals | 24 hours |
| Option-chain snapshots | 15 minutes |
| Successfully parsed XBRL filings | Permanent, keyed by symbol, attachment and filing metadata |

Fresh cache entries skip source reads/refetching. Fundamentals refresh after 24 hours; use refresh immediately when a new filing is known to have appeared. Failed fetches are not cached. Local snapshot files are read directly, with their existing quote-age validation. Parsed filing cache entries survive fundamentals expiry. New attachment URLs/metadata get separate entries.

Use the UI **Refresh sources** checkbox or global `--refresh` to force refetch, including parsed filings:

```bash
python3 -m results_predictor --refresh predict --date-range 2026-11-09:2026-11-13 --offline
python3 -m results_predictor --refresh score --auto
```

`RESULTS_PREDICTOR_DB` changes the application DB; `RESULTS_PREDICTOR_REPORTS` changes the default audit CSV directory. Back up the database, audit CSV, and cache together if you need to retain the complete workspace.

## Optional CE/PE analysis

`options_context/suggestion.py` adds an illustrative line only when valid options context exists, liquidity is OK, and v2 lean is BEAT or MISS. It selects the nearest listed expiry covering the event and the nearest **listed strike** to the underlying: BEAT → ATM CE; MISS → ATM PE. A missing premium at that strike gives N/A rather than substituting a farther contract. Stale, missing, mismatched or thin context and non-directional leans give N/A with a reason.

The illustrative stop is: **exit if the premium falls 50% from entry OR the underlying moves against the option by 0.5 × the implied move**, whichever occurs first. For CE, the adverse underlying level is spot minus half its implied move; for PE it is spot plus half. These are analysis levels, not execution guarantees. Each line, including N/A, is labelled **“ANALYSIS ONLY — not a trade recommendation, no order placed”**. There is no sizing or order-placement code.

## Hosting

Deploy with **Render Free + Turso Free**, using the included [render.yaml](render.yaml) Blueprint. The ledger uses `turso_serverless`, a [remote HTTP driver](https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx). A remote HTTP driver is required for this deployment on ephemeral hosts: the authoritative database must live outside the web service filesystem. [Render Free disks are ephemeral](https://render.com/docs/free); local SQLite files cannot provide durable hosted storage.

Create your own Turso database and auth token, then follow [ENV.md](ENV.md) for every environment variable and the restart persistence verification plan. **Commit and git push these files before creating the Render Blueprint**, so Render can read `render.yaml` from your repository. Choose New Blueprint in Render, connect the repository, and supply `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, and your own `RESULTS_PREDICTOR_WRITE_TOKEN` when prompted. The Blueprint installs with `pip install .`, starts the Python server, and checks `/health`. `RESULTS_PREDICTOR_HOSTED=1` disables silent local database fallback.

The provider cache is disposable; cache and local reports can disappear on restart, redeploy or spin-down. The Turso ledger is the durable source of predictions. Reads are public by default; writes require the configured write token. Set `RESULTS_PREDICTOR_PUBLIC_READ=0` to require that token for report/export/backup API reads too.

**HF Docker is not a free deployment path for new Spaces:** [Hugging Face requires a paid plan for Docker and Gradio compute](https://huggingface.co/docs/hub/en/spaces-overview). The YAML frontmatter remains `sdk: docker` with `app_port: 7860`. The included Dockerfile can also run locally:

```bash
docker build -t results-radar .
docker run --rm -p 7860:7860 results-radar
# Optional port override:
docker run --rm -e PORT=8080 -p 8080:8080 results-radar
```

Live NSE/BSE mode is best effort: free hosts may block or throttle outbound requests. The UI defaults to offline/sample mode and reports coverage gaps when live data is unavailable.

## Tests

```bash
python3 -m pytest -q
```

Tests cover existing forecast parity, append-only triggers, actuals/score references, proxy calls, CSV preservation, cache reuse/expiry/refresh and option-strike guards. The offline HTTP test starts the server on a random localhost port and exercises pages, scanning, report JSON and CSV export. Only loopback sockets are permitted for that test; external networking remains prohibited.

## During results season

Start with a diagnostic and calendar scan. Date ranges are inclusive, in YYYY-MM-DD format. Supply no symbols for discovery:

```bash
python3 -m results_predictor doctor
python3 -m results_predictor calendar --date-range 2026-11-09:2026-11-13
python3 -m results_predictor predict --date-range 2026-11-09:2026-11-13 --out next_week.csv
python3 -m results_predictor status
python3 -m results_predictor score --auto
```

Prediction runs always save a timestamped CSV under `reports/`; `--out` writes an additional copy. Calendar runs also write a CSV there. The table shows each metric's E1, v2 expectation, call and reason. SQLite defaults to `data/results_predictor.sqlite3`; use `--db path.sqlite3` after a command, or set `RESULTS_PREDICTOR_DB`. `status` shows state counts, snapshot age, source, effective date, hash and last calendar scan. Run `score --auto` again after filings appear. Fetch failures do not count as missing filings and do not erase existing results.

`doctor` checks Python/dependency versions, a real SQLite write and fixture presence. Online, it makes exactly one homepage request to nseindia.com, without retries. `doctor --offline` makes no requests. An HTTP block is reported as a coverage gap.

## Offline demonstration

These commands make no network requests:

```bash
python3 -m results_predictor doctor --offline
python3 -m results_predictor calendar --date-range 2026-11-09:2026-11-13 --offline
python3 -m results_predictor predict --date-range 2026-11-09:2026-11-13 --offline
python3 -m results_predictor status --offline
python3 -m results_predictor score --auto --offline
python3 -m pytest -q
```

`tests/fixtures/` contains illustrative board meetings, an equity/index universe, financial records, an OIL option chain and a ban list. The calendar, universe, chain and ban list are **demo fixtures**, not authenticated current exchange snapshots. Offline forecasts use `data/sample_fundamentals.csv` (eight symbols from the existing package). Financial fixture records contain history, not fabricated target-quarter actuals; scoring therefore stays pending/NO_FILING when no actual is present. `data/sample_actuals.csv` is synthetic, for legacy scoring demonstrations only. Tests prohibit external networking; the HTTP integration test permits loopback only. Offline predictions are marked and are never auto-scored against live observations, or vice versa.

## Real versus best-effort coverage

The CLI requests the NSE board-meeting API with a Session homepage cookie warmup, filters for financial results, and intersects with equity F&O underlyings. NSE's underlying-information API is preferred, with fo_mktlots.csv as a fallback. Indices are excluded. SQLite stores universe snapshots with effective_date, fetched_at, source and a SHA256 of the canonical stored JSON; live and offline snapshots are separate. Snapshots older than 24 hours refresh. An unavailable universe stops discovery instead of substituting sample symbols. The effective date is the fetch date, not a reconstructed historical membership date.

A BSE secondary-calendar request is best effort. BSE often provides only BSE codes: without a verified NSE symbol/date mapping the CLI says the secondary source is unavailable. No secondary coverage is claimed in that case. Calendar states are CONFIRMED_NSE, SECONDARY_ONLY, DATE_CONFLICT, RESCHEDULED, CANCELLED and FILED. Conflicting, secondary-only, cancelled and already-filed events are printed by `calendar` but skipped for new forecasts. Rescheduling compares with the last stored date. A cancellation or filed status requires explicit source evidence; disappearance alone does not prove cancellation.

Fundamentals prefer NSE's quarterly financial-results API and linked XBRL. The reader recognizes explicit financial fields and a conservative subset of XBRL facts and INR duration contexts. It excludes segment contexts, annual/YTD periods, unknown bases and unsupported units. Missing EBITDA is never inferred. Unsupported filings can produce NO CALLs; this is not a complete parser for every issuer taxonomy or PDF filing. NSE/bank/insurance formats vary. Raw normalized inputs, source and filing_timestamp / available_at / fetched_at are preserved in SQLite fundamentals snapshots. Unknown availability is excluded from prediction history.

Consolidated history is preferred if available at the information cutoff, otherwise standalone. Accounting families are never stitched together. The newest eligible revision for a quarter is used. The cutoff defaults to now (IST), capped at results-date midnight; `--cutoff` allows reproducible historical runs. Current discovery data does not reconstruct what the calendar looked like at an earlier cutoff. Missing or thin history gives NO CALL with a reason.

`--screener` explicitly enables a Screener fallback when NSE yields no rows. It is OFF by default. The stdlib HTML reader has no verified filing timestamps, so those rows remain ineligible for strict point-in-time calls. Screener's operating profit is only an approximate EBITDA field. It is not a substitute for exchange filings.

All provider HTTP goes through one polite client: one in-flight request per host, random 3–5 second spacing, `(5,20)` connect/read timeouts, Retry-After handling, exponential backoff, at most three attempts. Persistent 403/429 raises StopNetwork and disables further requests to that host for the run. Other hosts may continue. Blocks and unavailable data appear in a **COVERAGE GAP** section. There are no proxies or captcha workarounds. Online adapters are best effort; passing offline tests does not prove current live NSE/BSE response compatibility.

## Model parity and scoring

`model_v2.py`, `model_old.py`, `features.py` and `predict.py` retain their existing logic. The prediction adapters wrap them. Three metrics are reported independently: sales, operating margin and PAT. E1 is the old seasonal-naive expectation (same quarter last year moved by median same-quarter changes); v2 forecasts from the latest YoY change with the existing guards. The locked thresholds are sales 5%, PAT 5%, margin 2 percentage points. Thin history, inconsistent accounting family, implausible forecasts and one-off guards can all cause NO CALL.

Predictions store their E1 and threshold at creation time. Auto-scoring uses those stored values, never today's model settings. Each metric has its own audit trail:

```
PREDICTED -> AWAITING_FILING -> FILED -> PARSED -> RECONCILED -> SCORED
```

Exceptions are NO_FILING, PARSE_FAILED and BASIS_MISMATCH; later runs may retry them. Future reporting dates wait in PREDICTED. Available target-quarter observations must match accounting basis. Missing metric values fail only that metric. Actual BEAT means strictly above the frozen E1; equality means MISS, preserving legacy scoring. The frozen forecast threshold controls the original forecast call, not the actual-label comparison. NO CALL rows have no hit/miss score. There is no price-direction prediction or guarantee of investment performance.

## Optional neutral options context

Default mode is `none`:

```bash
python3 -m results_predictor predict --date-range 2026-11-09:2026-11-13 --options-mode public
python3 -m results_predictor predict --date-range 2026-11-09:2026-11-13 --offline --options-mode snapshot --options-snapshot tests/fixtures/option_chain.json
```

`public` tries NSE's equity option-chain API; offline public mode reports unavailable data. `snapshot` reads local JSON or CSV. A snapshot must identify one matching symbol and a quote timestamp; stale (>24 hours), future, mismatched and undated snapshots are rejected with a gap. The bundled chain is dated demonstration data and becomes stale. A single-symbol fixture does not supply options for every discovered company. `broker` raises NotImplementedError with a clear message; no integration or secrets are accepted.

JSON accepts NSE's `records` shape with `symbol`, `timestamp`, `underlyingValue`, and `data` rows containing `strikePrice`, `expiryDate`, CE and PE objects. CSV columns are `symbol,timestamp,underlyingValue,strikePrice,expiryDate,CE_lastPrice,PE_lastPrice,CE_openInterest,PE_openInterest,CE_totalTradedVolume,PE_totalTradedVolume,CE_impliedVolatility,PE_impliedVolatility`.

Context includes implied_move_pct (ATM call+put premium / spot), hist_abs_move_pct and hist_n, atm_iv_pct, pcr_oi, expiry_dte and liquidity OK/THIN. It chooses the nearest quoted expiry covering the results date, not a weekday rule. IV is the mean of the two ATM IVs; PCR uses total put/call OI for that expiry. OK requires ATM OI and volume of at least 100 on each side: this is a descriptive heuristic, not an exchange rule. Historical event price moves are currently unavailable to the CLI, so historical fields stay missing/0. The context function accepts supplied historical moves for callers. Missing observations are never zero-filled.

Flags are only IMPLIED_MOVE_ABOVE_HISTORICAL, IMPLIED_MOVE_BELOW_HISTORICAL, THIN_LIQUIDITY, NEAR_EXPIRY_PHYSICAL_SETTLEMENT (within five calendar days) and IN_BAN_PERIOD. `signal` is always None. PCR does not imply direction. Quote source/timestamp and fetch time accompany saved context. Ban status comes from NSE's fo_secban.csv, never an inferred MWPL percentage.

Documentation checked 2026-11-05: NSE monthly stock expiry is the last Tuesday from September 2025, per [NSE FAOP/68747](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAOP68747.pdf), following [SEBI's May 2025 expiry circular](https://www.sebi.gov.in/legal/circulars/may-2025/final-settlement-day-expiry-day-for-equity-derivatives-contracts_94189.html). Ban triggers at 95% MWPL; the commonly stated exit is below 80%, with the exchange's precise rule **80% or below**, per [NSE Clearing position limits](https://www.nseclearing.in/risk-management/equity-derivatives/position-limits). These are dated documentation facts: verify current circulars before relying on them. Neither expiry weekday nor MWPL thresholds are hardcoded into context logic.

## Legacy commands

```bash
python3 -m results_predictor predict --symbol TATAPOWER --date 2026-11-11
python3 -m results_predictor predict --file data/symbols.txt --date 2026-11-11 --out preds.csv
python3 -m results_predictor predict --symbol OIL --date 2026-11-12 --cutoff 2026-11-05T22:26
python3 -m results_predictor score preds.csv data/sample_actuals.csv
python3 -m results_predictor fetch TATAPOWER OIL --out data/fundamentals.csv
```

Create your own one-symbol-per-line file for `--file`. Legacy symbol/file prediction uses the sample CSV by default, or `--data` for your own panel, and also writes to the immutable ledger. These local-data predictions are marked offline. Legacy CSV scoring shows the original scoreboard. `fetch` is an explicit Screener request and writes rows with unknown availability, requiring verified publication timestamps before strict calls. Its retained `--refresh` argument is accepted for compatibility; this explicit Screener fetch does not use the NSE provider cache.

Codex example prompt:

> Read README.md. Run the network-free tests, then run `python3 -m results_predictor predict --date-range 2026-11-09:2026-11-13 --offline --out reports/demo.csv`. Summarize per-metric calls, NO CALL reasons and coverage gaps. Preserve the existing model logic and thresholds. Do not make trade recommendations.
