# ADR-0010: Causal VWAP mean-reversion research

Status: Accepted for Phase 10. Architecture v1.0 section 9 governs the hypothesis;
portfolio, independent risk and execution boundaries remain unchanged.

Add an independent, versioned feature calculator under `features/vwap_reversion.py`.
It reuses the existing typical-price formula, validates canonical bars and calculates
session VWAP using only completed, received bars from the session opening. It does not
modify Phase 5 snapshot schemas or historical feature hashes.

Use the mean true range of the preceding 12 bars as a causal price-unit volatility scale.
This is an engineering research choice, not a calibrated standard deviation or Gaussian
z-score. Exclude the trigger bar from its own scale. Require a slowing impulse followed
by reversal in close-to-close movement, candle body and closing range location. Extreme
stretch alone is insufficient; volume exhaustion is not assumed from price deceleration.

Default to CHOP and router permission. A versioned research override may relax the
non-veto regime/family restriction only in RESEARCH/BACKTEST. It cannot bypass stale or
unhealthy inputs, zero risk allocation, RISK_OFF or OPEN_SHOCK. These two router states
are architecture-level vetoes; the alpha cannot turn them into order permission.

Use the existing SignalIntent, journal and JSONB projection with a new typed evidence
variant. Persist the feature measurements, definition, data hash, settings, liquidity,
universe/master/state hashes and release provenance. Existing event serialization remains
compatible. No schema migration or third-party dependency is required.

Freeze entry VWAP/scale for exit reference metadata. The exit observer identifies conditions
on subsequent completed bars without managing positions or executing orders. Independent
backtest promotion is absent in this phase: both settings and engine block LIVE.
