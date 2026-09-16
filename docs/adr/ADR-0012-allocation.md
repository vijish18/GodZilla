# ADR-0012: Auditable ensemble and proposed targets

Status: Accepted for Phase 12, implementing architecture sections 11-13 without creating
the independent risk engine or execution layer.

Use an explicit five-member AlphaId/AlphaRegistry with base budgets and versioned score
normalization anchors. Normalize to bounded ordinal strength, never calibrated probability.
Preserve every pair/basket as an indivisible candidate. Opposite directions on any stable
security reject all involved bundles; same-direction overlap retains the strongest candidate
with deterministic ID tie-breaking. Existing/pending exposure blocks additions in that security.

Performance health is separate from operational/risk health. Persist rolling metrics and bounded
multiplier history in `alpha_health`. Increase only after sufficient history, a full configured
interval and new completed-trade evidence, one bounded step at a time. Deterioration can cut
immediately. Cold research starts at a conservative configured value; losses cannot transfer
unused budget to another alpha.

Allocate Decimal equity fractions using fixed base budgets, regime permission/multiplier,
health, ordinal strength and volatility/correlation/sector penalties. Deterministic greedy
selection, hard linear cap clipping and a final cap check provide an auditable baseline,
not a globally optimal portfolio. Unused capital remains idle.

Output preserves existing/pending commitments and adds whole proposed groups. Explicit
timestamped liquidity, correlation and unit-gross loss estimates are mandatory inputs; missing
critical inputs block additions. Share rounding is deferred. Every output requires independent
risk and forbids execution authorization. LIVE allocation is blocked.

Migration 0005 adds health, ensemble and portfolio projections to the existing atomic audit
transaction. Optional event detail fields preserve old schema-1 hashes when absent. No broker,
order creation or additional dependency is introduced.
