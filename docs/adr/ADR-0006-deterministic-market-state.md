# ADR-0006: Deterministic market-state router

Status: Accepted for Phase 6 within Architecture v1.0, sections 5 and 19.

The router consumes causal feature snapshots and explicit timestamped system/risk
health. It produces six deterministic states, threshold evidence, candidate and previous
states, confirmation count, alpha permissions, directional sides and a gross-risk
multiplier bounded to [0, 1]. It never generates signals or orders.

Every decision retains the complete immutable rule configuration/hash, feature snapshot
and version hashes, health facts/reasons, session opening, timestamps, provenance and
prior decision hash. YAML/environment settings live under `market_state`. Numeric
defaults are unoptimized research/paper assumptions; the architecture does not prescribe
calibrated router thresholds.

Safety dominates smoothing: RISK_OFF, OPEN_SHOCK and HIGH_VOL enter immediately.
Ordinary transitions require distinct feature timestamps and minimum state duration.
Pending transitions intersect old/new permissions and use the smaller multiplier.
A pending direction reversal cannot retain opposing momentum permission. Mixed evidence
does not default to CHOP.

Migration 0003 projects market-state history atomically with the audit event. Dispatch
occurs after commit through EventJournal. Failure latches publication closed. Exact
retries preserve receipt timestamp and correlation ID. Legacy schema-1 state events
retain their canonical hashes when the new optional decision field is absent.

The router is scoped to one ordered market/context stream. Restart recovery replays
transition history; a fresh instance starts RISK_OFF. External risk/compliance authorities
must keep reporting their latched veto until their own recovery protocol permits release.
This phase provides their input boundary, not their implementation or live re-enable logic.

No ML, returns-based optimization or profitability claim is introduced. The baseline
comparison describes state/transition differences. OOS promotion remains a later phase.
See [Phase 6 contract](../phase-6-market-state.md).
