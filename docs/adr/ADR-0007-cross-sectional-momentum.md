# ADR-0007: Alpha A cross-sectional momentum

Status: Accepted for Phase 7 under Architecture v1.0 section 6.

Alpha A ranks synchronized causal snapshots across a point-in-time eligible universe.
It creates scored SignalIntent facts only. It has no broker, order, sizing or independent
risk implementation. Router family/side permissions are an upper bound; CHOP is disabled.

Five return ranks, directional trend quality and liquidity quality form the configurable
weighted score. Shorts complement directional components, while retaining positive
liquidity quality. Score is not capital allocation or a probability of profit. The
architecture leaves Alpha A weights unspecified; shipped values are explicit,
unoptimized research/paper hypotheses.

Universe, instrument master, feature context and provenance must agree and be available
at the evaluation time. Incomplete cross-sections are blocked instead of silently
changing the denominator. Duplicate tokens/symbols are ambiguous. Exact-value ties
receive mid-distribution ranks; a capped boundary tie group is excluded in full.

The optional five-minute exclusion compounds out the latest return independently for
stock, sector and NIFTY before calculating residuals. Current trend/liquidity confirmation
is retained. No future label is accepted by the generation API.

Schema-1 SignalIntent receives optional score/evidence fields; old canonical event
hashes remain unchanged. Migration 0004 adds alpha_signals. Audit and projection commit
atomically, with dispatch after commit. Stable alpha/time/instrument UUIDs and a database
alpha/symbol/time uniqueness constraint reject conflicting or opposite-side duplicates.
Revised experiments must use isolated research stores, not overwrite an existing signal.

Research quantiles join fully observed forward labels only after generation. Reports
retain experiment identity, parameters and input hashes, and separate long/short gross
returns. They do not simulate fills, costs, portfolio sizing or prove profitability.
