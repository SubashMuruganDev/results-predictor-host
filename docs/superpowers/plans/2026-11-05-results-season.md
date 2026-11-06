# Results season implementation plan

Goal: extend the existing package with discovery, provenance, persistent predictions and independent metric scoring while preserving model parity.
Architecture: immutable legacy model functions behind small wrappers; polite providers feed normalized basis-separated panels; SQLite stores snapshots, calendars, prediction payloads and state transitions. Offline fixtures are explicitly demonstrations.

1. Add network-forbidden contract tests for calendar reconciliation, normalization, host stop, options neutrality, scoring and offline CLI; confirm they fail before implementation.
2. Implement schemas, polite HTTP and NSE providers, date resolution and financial normalization.
3. Implement SQLite snapshots and audited scoring transitions using prediction-time E1 and thresholds.
4. Add neutral options context and terminal/CSV reports, connect date-range commands while retaining legacy commands.
5. Add install metadata, fixtures and documentation; run full pytest and editable install/offline smoke checks.

Constraints: stdlib, pandas, requests; existing model logic unchanged. No inferred financial values, no mixed accounting bases, no trading recommendations. Failures become explicit coverage gaps. Persistent host throttling stops all further requests to that host.
