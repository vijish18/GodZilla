# Phase 7: Alpha A contract

Use `CrossSectionalMomentum(settings.momentum, timezone).evaluate(observations,
universe, master, state, at)`. Inputs are MomentumObservation objects pairing each
FeatureSnapshot with its original FeatureContext, plus Phase 2 UniverseSnapshot and
InstrumentMasterSnapshot and Phase 6 StateDecision. `at` comes from the caller's Clock.
The engine returns immutable MomentumResult scores, intents and reason codes.

## Causal population

Feature entity IDs must be the instrument-master tokens. All long-eligible members must
have exactly one snapshot at `at`; older and future snapshots are excluded. Missing,
duplicate or extra current symbols block ranking. The long population is the common
ranking denominator, including stocks that may not be short-eligible. Default minimum
population is ten. Unknown/invalid mandatory features block the entire cross-section.
Present but failing liquidity/trend values remain ranked and cannot emit a signal.

Master and universe dates must match the NSE local day and receipt timestamps must not
be future. Context membership version, known/effective times and context hash must
match. Active status, surveillance, ambiguity, duplicate tokens/symbols and shared
code/config/data/feature versions are checked. Sector/NIFTY context must exist. Upstream
Phase 5 owns correctness of those benchmark mappings and causal values; preserve its
original context and snapshots for reproduction. Phase 2 owns current compliance and
eligibility. Alpha generation does not replace those services.

## Formulas and defaults

For each input value, percentile is `(number_less + 0.5 * number_equal) / N`. Ties use
exact finite values. Percentiles are unchanged by input order or strictly increasing
rescaling. Fully tied populations rank at 0.5 and generate no tail candidates.

Five cross-sectional rank components are stock returns 30/60/120m (weight 0.20 each),
stock minus sector 30m return (0.15) and stock minus NIFTY 30m return (0.15).
Residuals are benchmark-relative simple returns, not regression residuals. Trend quality
(0.05) averages stock VWAP distance, stock EMA20 slope and sector VWAP distance sign
scores: positive=1, zero=0.5, negative=0. Liquidity quality (0.05) averages
`1-spread/max_spread` and `1-quote_age/max_age`, clipped to [0,1]; it is zero if any
liquidity requirement fails. Weights are nonnegative, sum to one and require a positive
directional component.

LONG score sums weight times rank/quality. SHORT score uses `1-rank` for the five
return ranks and trend quality, but retains liquidity quality. Better liquidity never
penalizes a short. Directional strength is the normalized weighted sum excluding
liquidity. Its percentile selects the top/bottom 20% by default, subject to at most five
candidates per side and `floor(N * tail_fraction)`. A cap never splits an exact tie;
the boundary tie group is dropped. Rejected candidates are not replaced from outside
the original tail.

Long confirmation requires all three trend inputs positive; short confirmation requires
all negative. Liquidity requires spread in [0,12] bps, quote age in [0,3] seconds,
ADV >=100,000 shares and daily turnover >=10,000,000 currency units. These are
unoptimized research/paper values. No participation quantity is computed or assumed.
Later portfolio/risk stages must apply quantity-dependent liquidity constraints.

`exclude_recent_5m` defaults false. When true, each horizon return becomes
`(1+r_horizon)/(1+r_5m)-1` before residual computation, independently for all three
price series. The excluded interval is (t-5m,t]; the remaining interval is (t-h,t-5m].
Thus the window is shortened, not shifted to include older data. Current confirmation
features remain current. Missing five-minute inputs fail closed only when this toggle
is enabled. Session-reset warm-up still requires Phase 5's 120-minute return history.

## Permissions, audit and deduplication

TREND_UP permits only the long tail and TREND_DOWN only the short tail, subject to
explicit router family, side and positive-risk permissions at exactly `at`. CHOP,
OPEN_SHOCK, HIGH_VOL and RISK_OFF emit no momentum intents. No alpha flag can override
a router denial. `momentum.enabled` disables this module; orchestration must also honor
the existing mode/promotion-aware `alphas.cross_sectional_momentum` availability.

SHORT candidates must be in the daily short-token set and F&O eligible. Within a
cross-section every symbol is unique and only one side can be emitted. IDs are UUID5
over alpha, UTC timestamp and instrument token, independent of side. Re-evaluation is
deterministic. Persisting changed content for the same identity fails closed.

Every intent retains raw score inputs, percentiles, weights, side contributions,
confirmation reason codes, full alpha config/hash, master/universe/cross-section/state
hashes and code/config/data/feature provenance. Apply `alembic upgrade head`, then use
`publish_intent(journal, intent, correlation_id, received_at, emit)`. Audit and
alpha_signals write atomically before emit. Retry identical timestamps/correlation.
The database also rejects repeated alpha/symbol/timestamp combinations. All projected
signal retrievals verify against the event audit chain. This correctness-first read
currently scans the chain; query optimization is future work.

## Research harness

`quantile_diagnostics(result, labels, experiment, available_at, quantiles=10)` accepts
ForwardReturn labels separately after generation. Every ranked member must have one
label at the same forward endpoint, after the decision and no later than available_at.
It rejects duplicate, missing or not-yet-observed labels. ExperimentIdentity records
hypothesis, code/data provenance, search family/trials, optional seed and window role.

Reports contain per-quantile count, mean strength and mean forward return, top-bin gross
long return and negative bottom-bin return for shorts. Combined gross return is their
average (one unit total gross exposure). Equal-value ties stay together, so bins may be
empty; missing bin statistics are None. Parameters and result/label hashes are retained.
These diagnostics describe the ranked population, not realized fills or the subset
that passes current directional confirmation. No labels flow back into generation.

The deterministic ten-stock fixture yields one member per decile and verifies mirrored
long/short returns. It is a numeric test, not a profitability claim. Chronological OOS,
realistic costs, slippage and anti-overfit promotion remain Phase 14 work.
