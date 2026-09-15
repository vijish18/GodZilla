# ADR-0004: Durable event/state substrate

Status: Accepted for Phase 4. Date: 2026-09-15.

## Context

Architecture v1.0 sections 19 and 23 require PostgreSQL, explicit events and replay before
features or strategies. A failed critical write blocks entries. Backtests and live consumers
must share the same event representation, rather than two separately implemented domain paths.

## Event and version contracts

`EventEnvelope` is a frozen Pydantic schema-v1 model with UUID event/correlation IDs, source,
UTC-normalized aware occurrence and receipt timestamps, a discriminated frozen payload and
required code/config/data/feature provenance. Payload collections use tuples and frozen models.
Unknown schema versions or event kinds fail validation; no implicit migration guesses occur.
Order and fill payloads describe facts only and keep external broker IDs as separate strings.
They do not submit orders, authorize risk, calculate signals or implement an order state machine.

Canonical JSON preserves Decimal strings and sorted quality flags. SHA256 idempotency keys
encode a namespace and a JSON list of identity parts, preventing ambiguous concatenation.
Producers must retain their event ID and idempotency key on redelivery. Reusing either identity
with conflicting content is an error, not an upsert or automatic correction.

## PostgreSQL transaction boundary

`PostgresRepository.append` commits provenance, audit event, bar/quote projection and optional
system state in one SQLAlchemy transaction. Projection/state failure rolls everything back.
Duplicate exact delivery returns false without re-emitting an event or rewriting state. A
different event for an existing source/instrument/bar-start conflicts at the projection's unique
constraint and requires explicit data correction review.

A transaction-scoped PostgreSQL advisory lock serializes audit appends and commits across
connections. This is intentionally a simple V1 throughput tradeoff. It is not a live execution
leadership implementation. Sequence identity values may have gaps after rollbacks; replay uses
their order, not a contiguous numbering assumption. Receipt timestamps must be nondecreasing;
late market observations retain their old occurrence time and actual later receipt time.

The audit chain hashes previous hash, idempotency key and canonical envelope (including optional
state projection). Reads validate the chain and event digests before returning events. This
detects accidental corruption; without externally anchored heads or append-only database roles
it is not protection against an administrator rewriting the entire chain or truncating its tail.

`EventJournal` is the domain-facing critical write/dispatch boundary. It is UNKNOWN before a
successful operation, and latches UNHEALTHY on transaction or downstream dispatch failure. It
does not expose driver exception text, which may contain credentials or payloads. Commit
precedes dispatch. A crash after commit requires replay and idempotent consumers; exactly-once
external side effects are not claimed. There is no automatic retry or halt reset. Ambiguous
commit outcomes require reconciling stored event identity before recovery.

## Tables and repositories

The initial migration creates `instruments`, `bars_1m`, `bars_5m`, `quote_samples`, `audit_events`,
`system_state`, `provenance_versions` and `instrument_versions`; Alembic owns `alembic_version`.
Instrument snapshots preserve effective dates, original JSON and hashes. PostgreSQL NUMERIC
retains price/volume precision; timezone-aware columns retain UTC instants. Indexed normalized
columns support later queries and JSON payloads preserve the full contract and quality flags.
Foreign keys bind projections to committed events. SQL is confined to storage adapters and
migrations; domain code depends on repository protocols.

## Replay and tests

JSONL fixtures contain serialized envelopes in receipt order. Replay advances `ReplayClock`
monotonically, preserves ties in recorded order, skips identical redelivery and rejects
conflicting duplicates. It never sorts on market timestamps. PostgreSQL replay follows audit
sequence order; a caller may use the same event consumer as live dispatch.

Tests use PostgreSQL 17 in a randomly named disposable Compose project with a runtime-generated
password, ephemeral loopback port and tmpfs database. Each test uses a separate random schema.
Migration tests execute upgrade, downgrade and re-upgrade and compare ORM metadata. Ordinary
tests retain the network prohibition; only the explicit `postgres` marker can use the verified
loopback test database. CI runs the full disposable database test harness.

## Deferred work

The application lifecycle skeleton does not automatically activate a durable consumer. Wire
`EventJournal.health()` into the eventual entry gate; a successful earlier write is not an ongoing
database heartbeat. Durable consumer checkpoints/outbox delivery, raw-vendor archives, event-log
retention, production roles/backups and execution leader election remain separate work. Readback
currently validates the whole audit chain in memory and is intended for bounded V1 fixtures.
