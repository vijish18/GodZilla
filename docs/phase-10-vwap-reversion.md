# Phase 10: VWAP mean reversion

`VwapMeanReversion.evaluate(bars, liquidity, universe, master, state, at, mode)` evaluates
one instrument per call and returns an optional scored `SignalIntent` plus reason codes.
It is stateless and requires no broker or network service. Reuse it with the available
session prefix for historical evaluation or as each new completed bar arrives.

## Feature definition and warm-up

The separately versioned `ReversionFeatureVersion` defines `vwap-reversion-features-v1`.
Input bars must be canonical, complete five-minute equity bars with positive volume and
no quality flags, aligned contiguously from the versioned calendar session opening.
All bars must identify the same instrument/provider. Missing or duplicate intervals reject;
no interpolation, forward fill, centered windows or overnight carry is used. Future bars
and bars received after `at` are excluded before hashing and calculation.

- Typical price: `(high + low + close) / 3`, shared with the existing feature engine.
- Session VWAP: sum of typical price times volume divided by observed session volume.
- Volatility scale: mean of `max(high-low, abs(high-previous_close), abs(low-previous_close))`
  over the configured prior bars, excluding the current trigger bar.
- Stretch: `(current_close - session_vwap) / scale`.
- Impulse, decelerated impulse and reversal: the last three close changes divided by scale.
- Body: `(close-open)/scale`; closing location: `(close-low)/(high-low)`.

Warm-up is `scale_bars + 2` contiguous bars (14 by default); the default entry window starts
75 minutes after session opening. Degenerate scale/range and unavailable history reject.
This stretch is ATR-like standardization, not a statistically calibrated normal z-score.
Snapshots embedded in intent evidence carry the full feature definition and input hash.
Historical and incremental callers use the same pure calculation over the available prefix.

## Entry rules

Defaults under `vwap_reversion` in `config/base.yaml` are explicitly unoptimized research/paper
starting values. YAML/environment layering and configuration hashing include every threshold.

Require CHOP, router family permission, positive gross multiplier and fresh healthy system/risk
evidence with no entry block. The optional `research_regime_override` permits studying non-veto
states only in RESEARCH/BACKTEST. PAPER retains normal gating; RISK_OFF and OPEN_SHOCK always
block. LIVE always returns `LIVE_PROMOTION_REQUIRED` and configuration cannot enable the alpha.

Entries are limited by both configured minutes since opening and the calendar's final-entry,
flatten and close cutoffs, including special-session times. Universe/master snapshots must be
current and known, with an active, unambiguous, non-surveillance instrument. Shorts additionally
require the permitted short universe and F&O eligibility.

Require fresh, uncrossed, positive quotes from the same provider/instrument, normal spread,
known fresh ADV/turnover metadata and minimum liquidity. These are absolute liquidity screens;
monetary sizing and participation approval belong to later portfolio/risk phases.

For LONG, require negative extreme stretch, a material downside impulse, a smaller nonpositive
follow-on impulse, then a positive close change and candle body with a high closing location.
SHORT mirrors every signed condition. All must pass: a falling knife or rising spike with no
reversal is rejected. This version uses price-reversal evidence, not an inferred volume-exhaustion
claim. The score is bounded stretch intensity, not win probability or a quantity instruction.

## Exit references and audit

Metadata records partial VWAP convergence (80% by default), an adverse continuation level
(one entry scale by default), time stop (30 minutes, clipped to flatten), and entry regime.
VWAP and scale references are frozen at entry. `observe_vwap_exit` reports convergence,
adverse continuation, time-stop or state-change conditions from subsequent completed bars;
it reports unavailable inputs instead of inventing a fill. It never claims a position is flat.

`publish_intent` persists the typed feature/exit evidence using the existing atomic journal
and alpha projection. Signal identity is deterministic per alpha/instrument/trigger close.
Duplicates are idempotent and conflicting duplicates fail closed. No broker, order or risk
engine is introduced, and no schema migration is needed.

## Validation and limitations

Tests cover hand-computed VWAP/scale, long/short reflection, regime and override gates, refusal
without reversal/deceleration, delayed/missing/duplicate bars, future-append invariance, liquidity,
configuration hashes, exits, and PostgreSQL evidence round-trip/idempotency. Unit/property tests
are offline; `python scripts/test_postgres.py` runs all suites with isolated local PostgreSQL.

No backtest profitability claim or promotion gate is implemented here. Promotion still requires
chronological OOS tests, realistic costs/slippage/fills, parameter stability, selection-bias
diagnostics, untouched final windows and independent long/short evidence. Data providers remain
responsible for truthful point-in-time metadata. The engine does not deduplicate open positions
or implement averaging down; future portfolio/risk controls must reject prohibited additions.
