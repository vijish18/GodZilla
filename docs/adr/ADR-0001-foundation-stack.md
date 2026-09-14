# ADR-0001: Foundation stack and safety boundaries

- Status: Accepted
- Date: 2026-09-14
- Scope: Phase 1 repository foundation

## Context

GodZilla needs deterministic, testable foundations before market data, trading logic, persistence,
or broker integration exists. Architecture v1.0 requires Python 3.12, typed layered configuration,
explicit operating modes, injected time, reproducible decisions, structured logging, fail-closed
LIVE activation, one active live execution leader, and strict separation between alpha and broker
access.

## Decision

Use a Python 3.12 `src` package with Pydantic v2 and pydantic-settings. Configuration layers in
deterministic order: base, selected mode profile, compliance profile, then `GODZILLA_`
environment overrides. Hash the canonical effective non-secret configuration with SHA-256.
Secrets are environment-only `SecretStr` values and are excluded from dumps and hashes.

Use a runtime-checkable `Clock` protocol with UTC `ProductionClock` and controllable
`FixedClock`. Domain and lifecycle code receive clocks by dependency injection.

Use distinct immutable UUID wrappers for signal, trade, event, and correlation identifiers.
Broker identifiers remain external values and must not reuse these internal types in later phases.

Use an explicit lifecycle state machine with immutable transition records. HALTED and EMERGENCY
states cannot return directly to READY, preventing an automatic safety bypass. JSON logging
redacts sensitive keys and common inline credential forms. Generic component health aggregates
to the worst status and separately reports whether a failure blocks new entries.

The default mode is PAPER. The checked-in LIVE profile remains invalid until a release, deployment
approval, explicit LIVE enablement, leader coordination, current compliance facts, and required
credentials are supplied. Configuration validation also preserves V1 constraints around market
orders, reconciliation, single execution leadership, and leverage.

CI checks formatting, lint, strict mypy, unit tests, and at least 85% foundation coverage. It has
no deployment job. The initial Docker image runs as a non-root user and contains no credentials.

## Consequences

Foundation components are replaceable and deterministic in tests. PAPER startup is safe by
default, while LIVE activation requires affirmative configuration. Later phases must preserve the
core flow and may add dependencies only behind explicit interfaces.

The current configuration values are unoptimized research/paper starting points. This ADR does
not approve broker connectivity, market-data APIs, trading logic, persistence, cloud deployment,
or LIVE trading.
