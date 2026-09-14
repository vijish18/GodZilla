# ADR-0003: Causal market-data ingestion and replay

Status: Accepted for Phase 3. Date: 2026-09-15.

Architecture v1.0 sections 15, 16 and 22 require immutable raw observations, canonical
five-minute bars, freshness vetoes and explicit recovery after disconnects. Database storage
belongs to Phase 4. This phase introduces storage protocols with in-memory implementations.

## Time and availability

Bars represent half-open `[start, end)` intervals. Domain timestamps are timezone-aware and
normalized to UTC. The calendar supplies Asia/Kolkata session open and market close, including
confirmed special sessions. Data continues through exchange close regardless of entry cutoffs.
One-minute bars retain both event time and receipt time. An observation is available only when
both its close and receipt are at or before the evaluation clock. Historical providers must
honor the request's `as_of` even if the clock advances during retrieval.

Canonical five-minute windows align to session open. No interpolation, forward fill, zero fill,
or cross-provider merging occurs. A complete window needs five distinct minute starts, all
marked complete and received by the cutoff. Incomplete windows retain observed OHLCV and flags;
`require_strategy_ready(at)` rejects them and any quality-flagged window. Completely absent
windows have no invented OHLCV: scheduled `window_health` detects them from expected minutes.

## Data and provider boundaries

`MarketDataProvider` supports one-minute historical bars and execution quote snapshots.
`StreamingMarketDataProvider` is an optional capability with serialized callbacks and explicit
subscription closure. Index, sector and VIX identifiers use `ContextFeedProvider` and `FeedKind`
under the same timestamp/availability contract. Vendor IDs are external metadata; no broker SDK
or order method is available. Volume screening conservatively flags zero volume, including
context feeds: a vendor without volume requires a reviewed normalization policy before those
bars are cleared for strategy use.

The adapter boundary must persist exact vendor bytes using `RawStorage` before decoding.
Normalized observations use a separate `NormalizedStorage` port and normalization version.
Raw IDs are immutable; conflicting reuse fails. Memory implementations are test doubles, not
durable production storage. Numeric invalidity is retained as flags for investigation; malformed
timestamps, nonfinite numbers and inconsistent durations fail model construction.

## Health and recovery

Quote freshness is recomputed from exchange timestamps on every health/read request. Missing,
future, stale, crossed, nonpositive and duplicate/out-of-order observations block availability.
Adapters must not stamp delayed quotes with current time. Duplicate bars are flagged and their
volume is not counted twice. Conflicting corrections are quarantined, never silently substituted.

A disconnect latches stream health unhealthy. Bounded exponential backoff only proposes retry
times; it neither sleeps nor connects. A successful connection requires a new fresh snapshot
and explicit gap reconciliation before data recovery validates. This validates data only; it
does not resume strategies, clear risk halts or enable LIVE. Applications must aggregate stream,
quote and bar-window health continuously.

## Determinism and limits

Replay YAML declares schema version, snapshot ID, source, receipt times and normalized records.
The synchronous fake emits in receipt order and advances an injected fixed clock; EOF disconnects.
The CLI reports input/calendar/config hashes, software/normalization versions and canonical output
hash. Quality flags serialize in sorted order. Unit/integration/replay tests forbid socket access.
Configuration hashing also canonicalizes unordered sets before serialization; a cross-process
hash-seed regression protects the compliance order-type and universe exclusion collections.

No vendor-specific connectivity, durable writes, corporate-action adjustment, feature engine,
strategy, execution or promotion logic is implemented. In-memory caches are session/replay scoped;
bounded production retention and PostgreSQL/event storage are Phase 4 prerequisites. Gap and
volume thresholds are documented research/paper starting values, not optimized parameters.
