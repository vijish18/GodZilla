# Phase 6: market-state contract

Instantiate `MarketStateRouter(settings.market_state, calendar, timezone, metrics)`.
Call `route(feature_snapshot, routing_health, at)` chronologically for one stable market
context. Supply `at` from the caller's Clock/replay path. Exact duplicates return the
previous result without changing counters. Same-time conflicts and time regressions
raise; callers must block processing on errors. Health updates can force RISK_OFF
between feature arrivals. Reusing a feature timestamp cannot advance confirmation.
New sessions and gaps over 600 seconds reset pending confirmation.

## Version-1 rules

System and risk health must explicitly be HEALTHY with no veto, observed within 30
seconds. Features must be no more than 300 seconds old, never future-dated, and belong
to the confirmed active calendar session. Failure overrides classification with RISK_OFF.
Health defaults to unknown/blocked; the SystemHealth adapter uses the oldest observation.

Classification priority after the safety guard:

1. OPEN_SHOCK in the first 900 seconds inclusive, or within 1800 seconds when absolute
   opening gap is at least 1.5% or a volatility threshold fires.
2. RISK_OFF for missing required features or breadth coverage other than 100%.
3. HIGH_VOL for realized volatility >=0.005, normalized range volatility >=0.01,
   or available VIX >=25.
4. TREND_UP for VWAP distance >=0.001, 15-minute slope >=0.0002, 30-minute slope
   >=0.0001 and advancing breadth >=0.60. TREND_DOWN mirrors signs and uses declining breadth.
5. CHOP for absolute VWAP/slope values no greater than those bounds and both advancing
   and declining breadth <=0.60, with contained volatility. Otherwise RISK_OFF.

Required Phase 5 `market.*` values are VWAP distance, slopes 15/30m, realized/range
volatility and advancing/declining breadth plus coverage. Opening gap is required
inside the shock window after stabilization. VIX is optional; its missing reason is
audited. A price-only index feed with unavailable VWAP remains blocked. Phase 5 warm-up
can keep routing blocked beyond opening stabilization; no substitute values are invented.

Version 1 uses existing slope and breadth features, not new VWAP-cross counts or sector
participation indicators. Sector confirmation and pair stability remain later alpha
prerequisites. Qualitative architecture evidence examples are not treated as calibrated rules.

## Permissions and transitions

TREND_UP/DOWN permit momentum, breakout and sector relative value at multiplier 1;
momentum/breakout sides are LONG/SHORT respectively. CHOP permits pairs, VWAP reversion
and sector relative value at 0.5. HIGH_VOL permits only sector relative value at 0.25;
pairs remain excluded without validated relationship evidence. OPEN_SHOCK/RISK_OFF
grant no permissions and multiplier 0. Side restrictions apply to directional engines,
not neutral strategy legs.

Ordinary transitions and recovery require two distinct feature timestamps and 600
seconds in the current state. Safety-state entry is immediate. Pending transitions
intersect permissions and take the smaller multiplier, possibly blocking all families
before the state label changes. Mode-specific alpha availability, research promotion,
liquidity, portfolio and independent risk gates still apply.

All numeric parameters are versioned in `config/base.yaml`, with overrides such as
`GODZILLA_MARKET_STATE__CONFIRMATION_COUNT=3`. They are research/paper hypotheses.
Formula revision 1 fixes priority, inclusive boundaries and family mapping. Formula
changes require a new revision and review.

## Audit and research APIs

MarketStateEvidence records feature values, missing reasons, operators, thresholds,
pass/fail outcomes, health and session information. Trend/stress checks are always
recorded; CHOP checks are recorded when that branch is reached. Confirmation/duration
checks explain smoothing. Provenance is inherited from the snapshot: callers must use
the same deployed code/config for features and router and retain input snapshots and
calendar versions for reproduction.

Apply `alembic upgrade head`. Call `publish_state(journal, decision, correlation_id,
received_at, emit)` with actual receipt time. Audit/history commit before emit. A
computed but uncommitted decision must not reach downstream consumers. Retry the
identical event; persistence failure requires reviewed recovery. Retrieve decisions
by hash through MarketStateRepository. Restart with replay, not merely the last label.

Metrics cover decisions by state, transition pairs, state duration, suppressed
transitions and flicker (a change within 900 seconds of the prior change). Replay
uses a fresh metrics sink to avoid double counting production observations.

`compare_baseline()` accepts chronological ResearchObservation inputs, settings,
calendar/timezone and ExperimentIdentity. It records hypothesis, code/data snapshot,
parameter set, search family/trials, optional seed, window role and prior inspection.
The baseline uses VWAP plus 15-minute slope with shared health/calendar/opening guards;
it omits broader classification and confirmation. Outputs are state sequences/counts,
agreement and transitions. No optimization, return labels or P&L are computed.

The eight-observation alternating fixture has seven baseline changes and zero confirmed
router changes (RISK_OFF throughout). This verifies smoothing and its opportunity cost,
not economic improvement. Untouched final windows, realistic costs and profitability
assessment remain backtest-phase work. Run the market-state unit/property suites and
`python scripts/test_postgres.py` for complete database validation.
