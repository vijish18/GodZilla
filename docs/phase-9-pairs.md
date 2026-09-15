# Phase 9: Statistical arbitrage / pairs

All thresholds in `pairs` configuration are research/paper starting values, not optimized
parameters. `pairs.live_enabled` can only be false; enabling the pairs alpha in LIVE
configuration also fails validation. The engine rejects LIVE regardless of configuration.

## Inputs and causality

`PairCandidate` records distinct legs, economic tags, sectors, source, reviewed/known times,
effective dates and an adjustment snapshot with explicit corporate-action clearance.
Candidate knowledge/review must precede the current session opening. Providers remain
responsible for truthful point-in-time adjustment, liquidity and instrument metadata.

`PairsEngine.evaluate` takes observations, two liquidity snapshots, point-in-time universe
and instrument snapshots, router evidence, an explicit timestamp/mode and optional research
`PairPosition`. It makes no network calls. The position is supplied research context, not
a reconciled broker position or production position book.

Warm-up requires `training_bars + 1` valid canonical five-minute paired observations.
Both bars must have closed and been received. The final observation is scored against
the preceding training window; it never participates in its own hedge or spread fit.
Calendar sessions determine expected intervals across days, without overnight gap filling.
Missing, duplicate, misaligned, incomplete or stale bars fail closed. Future observations
are excluded before window selection and input hashing. No centered windows or future fills.

For training log prices L and R, OLS estimates `L = intercept + beta * R`.
Spread is `L - beta * R`; z is `(current_spread - training_mean) / training_population_std`.
The intercept is therefore represented in the spread mean. Diagnostics include cointegration,
spread stationarity, level-series stationarity screening, split-window beta drift, spread
variance ratio and mean shift. Half-life is `-log(2)/log(phi)` for a valid AR(1) coefficient
between zero and one, measured in trading bars; maximum holding time is wall-clock minutes.
Degenerate or unavailable statistics reject the relationship; no NaN substitution is used.

## Decisions and publication

Entry requires router permission, liquidity/freshness, stable statistics, eligible legs and
time before final entry. The actual short leg also needs F&O and short-universe permission.
Positive z sells the spread; negative z buys it. Weights are `1/(1+beta)` and `beta/(1+beta)`.
Net/gross imbalance is capped, but this does not establish market-factor neutrality.

Exit references retain the entry fit. Prior-bar rolling diagnostics can trigger a structural
exit. Other reasons include reversion, adverse spread, max hold, session flattening, withdrawn
state permission and deteriorating liquidity. Ambiguous data or unavailable diagnostics with
an open position sets `open_risk_attention`; it cannot invent an executable exit price.
An exit intent is not permission to execute through an unavailable or unsafe market.

`PairSignalIntent` carries both legs, entry/exit reason, frozen fit, combined risk reference,
settings and provenance. `publish_pair_intent` persists atomically before dispatch. Duplicate
publication is idempotent; conflicting duplicates or critical persistence failure halt the
journal. The shared repository reads the projection against its audited event. The projection
uses `PAIR:<pair_id>` as its symbol; its score is normalized z intensity, not confidence.

## Research reporting

`pair_research_report(evaluations, trades, scenarios, experiment)` returns a serializable
`PairResearchReport`. Supply chronological evaluations per pair, externally observed signed
leg returns over common holding intervals, and explicit one-way costs in basis points.
The report retains experiment identity, settings, evaluation hashes, trade inputs and scenarios.
It reports acceptance, uninterrupted survival of the initially accepted cohort, beta drift,
half-life and gross/cost/net scenario means. A failed pair does not regain survival by recovering.
No initial accepted cohort reports survival zero. Empty trade inputs report null return metrics.

Round-trip cost is `2 * (left_weight * left_bps + right_weight * right_bps) / 10000`.
These are supplied scenarios, not hard-coded fees, fill simulation or profitability evidence.
The report does not implement a backtester or independently validate trade labels. Promotion
still requires chronological OOS evidence, realistic costs, multiple-testing controls and an
untouched final test window. Independent validation and future paired-execution controls remain
prerequisites; this phase cannot enable LIVE.

Tests use seeded synthetic related/unrelated prices, hand-calculated OLS/z/cost values,
future-append invariance, sign symmetry and PostgreSQL atomicity/idempotency. Run offline tests
with `python -m pytest -m "not postgres"`; `python scripts/test_postgres.py` runs the full suite
and coverage against an isolated local Docker PostgreSQL instance.
