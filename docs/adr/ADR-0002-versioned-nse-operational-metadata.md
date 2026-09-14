# ADR-0002: Versioned NSE operational metadata

- Status: Accepted
- Date: 2026-09-15

## Context

Session calendars, broker permissions, surveillance status, instrument eligibility, and API
rules change independently of application code. Treating any of them as timeless would make a
historical decision irreproducible and could allow an entry under unknown rules.

## Decision

Calendar, compliance, instrument-master, and broker-symbol facts are immutable typed snapshots
with versions, source references, review/effective dates, and local provider interfaces. Dates
and instruments live in YAML data, never Python branches. A pending special session is closed
until confirmed times arrive. Dates outside calendar coverage fail rather than infer a session.

PAPER and LIVE startup pass a clock-driven compliance gate. An incomplete, ineffective, or
expired profile blocks startup. The included profile authorizes only the local paper simulator;
it deliberately cannot qualify LIVE. A future broker adapter must supply and verify its own
profile without changing these domain models.

The daily universe must use an instrument snapshot for the same date. Suspended, untradable,
surveillance, ambiguous, future, and configured exclusions carry explicit reason codes. Short
membership requires F&O eligibility plus compliance and broker permission. Stable UUIDv5 IDs
cover the inputs and results.

## Consequences

Operational metadata can be replayed and audited without a broker SDK or network access. A
missing snapshot or unreviewed change prevents new entries. Operators must refresh and review
snapshots before their coverage or compliance expiry; automated live providers remain a later
phase.
