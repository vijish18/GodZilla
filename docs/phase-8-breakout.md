# Phase 8: Alpha B momentum breakout

`MomentumBreakout(settings.breakout, calendar, timezone).evaluate(observations,
universe, master, state, at)` returns SignalIntents and per-candidate reports.
Each BreakoutObservation contains current and reference feature/context pairs, the
trigger bar, preceding canonical bar and FeatureSetVersion. Inputs are replaceable
local domain objects; this module performs no network, broker or SQL calls.

## Availability and permission

Use one synchronized current snapshot per member of the daily long-eligible population,
with a default minimum of ten. Missing/duplicate members block the cross-section. Future
snapshots are ignored. Universe/master timestamps and dates, active status, surveillance,
ambiguity, identity, feature/context hashes and code/config/data provenance are validated.
The reference and current contexts must use the same instrument and benchmark sources.
Shorts additionally require short-universe membership and F&O eligibility.

The router must explicitly permit breakout and the intended side, with positive gross
permission at `at`: TREND_UP selects LONG and TREND_DOWN SHORT. Other states are disabled.
`breakout.enabled` provides an additional module switch. Orchestration must also respect
mode/promotion-aware alpha availability, session entry cutoffs and later portfolio/risk
validation. A regime permission is not an order approval.

Both bars must pass Phase 3 canonical quality checks. The preceding bar ends at trigger
start, and the trigger aligns to the confirmed exchange session. Its end/receipt must
be available at evaluation, at most 30 seconds after bar close. Reference feature time
must be within that session and no later than trigger start. Feature/definition versions
must match. Opening range needs the configured number of completed opening bars; swings
need at least `2*confirmation_bars+1` bars. Phase 5 supplies the actually confirmed swing
point; the router does not reinterpret an unconfirmed extremum as a valid level.

## New break and confirmation

For LONG, preceding close must be <= a known range high and trigger close strictly > it.
SHORT mirrors around a known low. Already-beyond levels do not generate repeated
continuation signals. `range_source` is opening_range, swing or either. If both cross,
use the hardest crossed level (highest LONG barrier / lowest SHORT barrier); opening
range wins equal levels. Current-bar range values never replace the reference level.

Confirm stock close above/below session VWAP, sector VWAP distance and stock EMA20 slope
in the same direction. Both signed 30-minute residual returns versus sector and NIFTY
must be >=0.002. Their cross-sectional mid-distribution ranks are averaged; shorts use
one minus this average. Require directional strength >=0.8 and relative volume >=1.5.
Ties receive equal ranks; uniform residual populations rank 0.5 and fail the tail gate.
Ranks are independent of input order and Alpha A's settings or selected signals.

Liquidity requires spread in [0,12] bps, age in [0,3] seconds, ADV >=100,000 shares,
and estimated daily turnover >=10,000,000 currency units. Quantity-dependent ADV
participation is not estimated without a proposed size; later portfolio/risk gates
must enforce it. Missing required rank inputs block the batch; missing other mandatory
confirmation features reject the individual candidate with a reason code.

## Breakout quality and score

Let `s=+1` for LONG and `-1` for SHORT, `L` be the known level and ATR the current causal
feature. Extension is `s*(close-L)/ATR`, required between 0.05 and 0.75 inclusive.
Directional close location is `(close-low)/(high-low)` for LONG and its complement for
SHORT; require >=0.7. Directional body is `s*(close-open)/(high-low)`; require >=0.2.
Zero ATR and flat bars are rejected. Quality averages close location, directional body
and extension divided by 0.25 (capped at one). The maximum extension rejects chasing.

The seven normalized [0,1] components and default weights are:

- Relative strength/weakness rank: 0.25.
- Breakout quality as defined above: 0.20.
- Relative volume / 3: 0.15.
- Signed sector VWAP distance / 0.005: 0.15.
- Signed stock EMA20 slope / 0.001: 0.10.
- Geometric reward/risk proxy / 3: 0.10.
- Mean of `1-spread/max_spread` and `1-age/max_age`: 0.05.

Each normalization is clipped to [0,1]; weights must be nonnegative and sum to one.
The proxy is `2/(extension+0.1)`: a hypothetical two-ATR reward distance divided by
distance to the buffered invalidation level. Require proxy >=1.5. This is a scoring
geometry convention, not an estimate of achievable reward or a stop-loss order.
The default minimum weighted score is 0.6. All values are editable under `breakout`
in layered YAML/environment config and hashed; none is claimed optimal.

## Invalidation and audit

Invalidation level is `L-s*0.1*ATR`. Metadata expires after three canonical bars, capped
at session close. `observe_failed_breakout(evidence, later_bar, at)` reports FAILED
when a subsequent completed bar closes back through that buffered level, ACTIVE when
that observation holds it, EXPIRED beyond the window, or UNAVAILABLE for an unfinished
or non-subsequent bar. The observer checks instrument/source identity. It is pointwise,
not a latched trade state machine: downstream research must retain prior failures and
account for gaps rather than interpret a later ACTIVE observation as recovery.
It does not place an exit or make a portfolio/risk decision.

Signal evidence persists every normalized component, raw measurement, weight and
contribution; pass/fail threshold checks; invalidation metadata; full config/hash;
observation/cross-section/universe/master/state hashes; and code/config/data/feature
provenance. Publish with the shared `publish_intent()` journal API. Migration 0004 from
Phase 7 already contains the required table. Audit and signal projection commit before
dispatch; exact retry is idempotent and conflicting same-alpha/symbol/time data fails
closed. Existing Alpha A and legacy signal serialization remain compatible.

Signal identity is based on alpha, trigger-bar end and instrument, independent of side.
Reevaluating the same trigger cannot create another accepted identity; changed evidence
requires reviewed reconciliation with the existing audit, not blind republication.
This phase emits hypotheses only. Costs, fills, slippage, OOS diagnostics and promotion
remain later-phase work.
