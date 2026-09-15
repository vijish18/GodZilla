# ADR-0008: Alpha B momentum breakout

Status: Accepted within Architecture v1.0 section 7; Phase 8 only.

Alpha B independently ranks sector/NIFTY residual momentum and requires a new closed-bar
cross of a previously available opening-range or confirmed swing level. It consumes
versioned features, their contexts/definition, canonical trigger/previous bars, daily
universe/master snapshots and router permissions. It does not consume Alpha A signals.

The architecture's seven weights are configurable research starting values. All other
quality, confirmation and normalization thresholds are explicit versioned engineering
hypotheses. Reward/risk is a geometric proxy using an ATR distance, not expected P&L,
forecast reward, executable stop or position sizing.

The level snapshot must be available before the trigger bar starts. Opening-range and
swing minimum warm-up are additionally checked against the session and feature definition.
No unfinished bar or current-bar-derived level may trigger a signal. Each side is an
exact sign mirror for direction-dependent calculations.

Failed-breakout evidence includes the broken level, buffered invalidation level,
confirmation/trigger times and observation expiry. A pure subsequent-bar observer can
describe a return through the level; it does not close a position or dispatch an order.

SignalIntent now accepts either Alpha A or Alpha B evidence. Alpha A's serialized format
and legacy schema-1 hashes remain unchanged. Shared publication and the existing
alpha_signals table atomically store Alpha B components, checks and invalidation
metadata. No new migration is needed. Alpha/time/instrument UUID and alpha/symbol/time
database uniqueness rules prevent conflicting duplicate publication.

No broker, independent risk, allocation, execution or profitability promotion is added.
