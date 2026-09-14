# PROJECT GODZILLA

Production-oriented, scientific, multi-alpha intraday long/short quantitative trading platform for NSE India. The objective is high risk-adjusted returns subject to capital-survival, liquidity, execution and operational constraints. No fixed daily-return promise is made.

## Status

Repository initialization only. Phase 1 engineering foundation is not yet implemented or verified. No broker connectivity, market data APIs, trading logic, deployment or LIVE trading is enabled.

## Source of truth

`GodZilla_End_to_End_Project_Architecture_v1.0` is authoritative and must be read completely before implementation code is changed. Architecture changes require explicit owner approval. The document has not yet been read in this coding session and is not included in this initial commit.

## Required system flow

Market data -> features -> market-state router -> independent alpha engines -> alpha ensemble -> portfolio allocator -> independent risk engine -> execution engine -> broker adapter.

Strategies never call brokers directly. Every new order must pass portfolio and independent risk validation. Risk rejection cannot be overridden. Ambiguous submissions require reconciliation before retry. Risk-critical uncertainty fails closed. Exactly one active live execution leader is allowed, and V1 finishes each normal session flat.

## Phase 1 scope

- Python 3.12 package under `src/godzilla`.
- Typed Pydantic settings, layered YAML and environment overrides, validation and reproducible configuration hashes excluding secrets.
- RESEARCH, BACKTEST, PAPER and LIVE modes, with PAPER as default; LIVE prerequisites must fail closed.
- Explicit system states, injected timezone-aware clocks, UUID-backed internal IDs, redacted structured logging, health primitives and lifecycle/CLI skeleton.
- Development Docker configuration, Makefile and GitHub Actions quality checks without deployment.
- Deterministic foundation tests with at least 85% coverage, README and ADR-0001.

No broker integration, market data API or trading logic belongs in Phase 1. Session timing defaults must come from the architecture and satisfy `final_entry < flatten_start < hard_flatten_deadline`. Research/paper starting values are not optimized values.

## Required verification

Before declaring Phase 1 complete, run:

```text
ruff format --check .
ruff check .
mypy src
pytest (relevant suites and foundation coverage >=85%)
git diff review
secret scan / obvious credential check
```

These checks have not been run. This initialization commit contains documentation only. Stop after the Phase 1 report; do not automatically begin Phase 2.

## Security

Never commit secrets or credentials. Broker IDs remain conceptually separate from internal IDs. Deployment success does not imply LIVE trading enablement. Holidays, instrument tokens, fees and compliance rules must not be hard-coded.
