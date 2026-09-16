# Phase 12: Ensemble and portfolio allocation

The output is a proposal requiring independent risk validation, never a broker order or execution
approval. Settings are research/paper starting values, not optimized parameters. LIVE allocation
is blocked. Domain modules contain no SQL, network calls or direct wall-clock reads.

## Registry and normalization

`AlphaId` identifies all five implemented alphas. `AlphaRegistry` requires each exactly once
and total base budgets <=1 equity unit (.2 each initially). Versioned floor/ceiling anchors
linearly map scores to [0,1]. Pair strength is absolute z/stop-z; relative-value strength is rank
separation. These provide a common ordinal research scale, not cross-alpha probability calibration.
Anchor selection belongs to training-only research, not future outcome fitting.

`normalize_intents` checks current timestamps, state/code/config/data provenance, point-in-time
master/universe and short eligibility, and resolves stable listing IDs. Whole pairs/baskets remain
indivisible. Pair EXIT intents do not become new entries; exit/reduction lifecycle handling remains
a later portfolio/risk/execution responsibility. Exact duplicate IDs deduplicate; conflicting
payloads under an ID block evaluation. Opposite directions on a shared security reject all affected
bundles. Same-direction overlaps retain highest strength, breaking ties by UUID. Rejections retain
IDs and reasons. No same-symbol netting or averaging down is assumed.

## Rolling health

`measure_health` includes completed TradeOutcome records only when both close and receipt are
known. Deduplicate by trade ID, sort by close/ID and keep the rolling window (100 trades). Conflicting
duplicates reject. Gross P&L must be before separately supplied nonnegative costs and slippage
debits, in the same currency as gross capital (INR). Do not subtract slippage twice when preparing
inputs. No NSE fees are hard-coded.

Net P&L = gross P&L - costs - slippage. Net expectancy is mean net P&L/gross capital per trade.
Drawdown is peak-to-trough decline in the additive sequence of normalized trade returns, not actual
account equity drawdown. Report trade count, net P&L, costs/slippage and regime counts. Actual account
drawdown remains an independent risk responsibility.

Default multiplier bounds [0,1], cold start .5, minimum history 20, upward step .05, interval 86400
seconds. Increases require positive expectancy, acceptable drawdown and a newly closed trade.
Reusing outcomes cannot repeatedly increase it; missing/regressed history rejects and warm-up cannot
retain a multiplier above the initial value. Nonpositive expectancy or excessive drawdown cuts to
the minimum immediately. The predecessor must match alpha/settings and precede the new snapshot.
Snapshots link predecessor, outcomes hash, settings and release/config/data provenance. Missing,
stale or mismatched health blocks allocation. Performance health cannot override a system/risk veto.

## Allocation units and constraints

`allocate(AllocationInputs, ExchangeCalendar)` returns PortfolioDecision/TargetPosition records.
Fractions are Decimal units of current equity, not share quantities or order deltas. Output includes
unchanged existing/pending commitments plus new proposals. Inputs include registry, normalized
candidates, health, complete portfolio, eligible liquidity capacity, unit-gross combined loss proxies,
prior-only correlations, state, caps, timestamp and mode. Input and calendar-session hashes identify
the reproducible decision. Providers must retain the referenced snapshots.

Existing/pending positions consume all relevant capacity. Existing cap violations return unchanged
targets with a risk-review reason; this does not claim to flatten them. Any held/pending/selected
security overlap rejects the entire new bundle. Missing/incomplete portfolio, stale state/health,
RISK_OFF/OPEN_SHOCK, zero gross multiplier, LIVE and calendar final-entry cutoffs block additions.
Risk rejection must remain in the blocking portfolio/state health inputs and cannot be offset by
score or P&L. Outputs always carry `independent_risk_required=true`, `execution_authorized=false`.

For alpha a and candidate i:

`desired = base_budget[a] * regime_multiplier * health[a] * strength[i] / max(1, sum_alpha_strength) / penalty`

`penalty = volatility_penalty * (1 + sector_penalty * existing_sector_groups + correlation_penalty * max(0, abs_corr-threshold)/(1-threshold))`

Use maximum absolute correlation against existing/pending/selected securities. Missing needed
cross-security estimates reject, never imply zero correlation. Correlation training ends before the
decision and no later than snapshot availability. This phase consumes estimates, not a covariance
estimator. Volatility penalty is >=1; unit-gross combined loss must be positive. Providers must supply
causal, independently reproducible loss and liquidity estimates. A loss proxy is not risk approval.

Clip whole-candidate gross to remaining alpha budget/cap, total gross, signed net, symbol, sector,
portfolio/candidate open risk and group limits. Liquidity notional is the minimum of supplied maximum,
ADV shares * price * participation and daily turnover * participation, divided by equity. Gross sums
absolute exposures; opposite holdings do not invent capacity. Apply loss per gross to the whole
bundle and distribute only its bookkeeping risk across legs. Quantize gross down to 1e-12 equity
units, preserve signed hedge weights and recheck portfolio caps before accepting.

Defaults: alpha .40, gross 1.00, net .60, symbol .15, sector .40, open risk .01, candidate risk .0025;
four groups, two per sector, participation .0025. Config validation prevents allocator bounds exceeding
existing portfolio/risk policy or registry enablement contradicting disabled alphas. Unused budgets
remain idle; losses/rejections never increase another alpha's base budget. Greedy allocation can
underuse capacity. Integer sizing, concurrent reservations and reconciliation remain later work.

## Persistence and tests

Migration 0005 creates separate `alpha_health`, `ensemble_decisions`, `portfolio_decisions` projections.
`publish_health`/`publish_decision` use the existing atomic audit transaction before dispatch. Repository
reads verify against the audit chain. Duplicates are idempotent; conflicts/critical failures halt the
journal. Generic Decision events keep `accepted=false` for proposals. Old event hashes remain stable
when new optional details are absent.

Tests cover all five adapters, conflicts/provenance, numeric health, slow increases, future outcomes,
determinism, baskets, existing/pending exposure, missing inputs, conservation/cap properties, migration
round-trip, persistence and failure. Run `python -m pytest -m "not postgres"` offline or
`python scripts/test_postgres.py` for the complete isolated PostgreSQL and coverage suite.
No profitability or LIVE promotion is established; Phase 13 must independently validate hard risk,
actual quantities and execution constraints before any later executable order path.
