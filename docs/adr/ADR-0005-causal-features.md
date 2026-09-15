# ADR-0005: Versioned causal feature snapshots

Status: Accepted for Phase 5 implementation within Architecture v1.0.

One deterministic engine computes both historical and incremental snapshots from
canonical five-minute bar and quote events. Availability is constrained by envelope
receipt time and observation receipt time. Effective, known-at context identifies
benchmark feeds and point-in-time universe membership. No market-state classification
or trading decision is introduced.

Feature definitions, context, input events and snapshots use canonical SHA-256 hashes.
Snapshots retain code commit, configuration hash and data snapshot provenance. Feature
parameters are explicit immutable `FeatureSetVersion` inputs supplied by the caller;
defaults are research/paper engineering starting values, not optimized parameters.

Missing values are `None` with a reason, never NaN, zero imputation or future fills.
Incomplete, duplicate, misaligned or quality-rejected session inputs invalidate the
affected series. Index, sector and VIX price features permit zero-volume observations;
volume-dependent values remain unavailable. Equity zero-volume bars remain rejected.

Migration 0002 adds feature definitions and snapshots. Publishing uses the existing
event journal, atomically writing audit event, version and snapshot. A critical write
failure rolls back and latches the journal closed. Snapshot-derived UUID/idempotency
keys make exact re-publication idempotent. Feature events use the snapshot evaluation
timestamp; callers must preserve the same correlation ID on retry. Existing schema-1
feature events without a snapshot remain readable through additive optional fields.

The reference engine retains events in memory and recomputes each requested snapshot.
This deliberately provides a transparent correctness baseline for a later bounded-cache
implementation. Throughput optimization requires equivalence tests against this path.
Publication is an explicit service call; the lifecycle does not automatically run it.

See [formula and missing-data contract](../phase-5-features.md).
