# Phase 3: market data operations

## Running an offline recorded excerpt

```powershell
.venv\Scripts\python.exe -m godzilla.market_data.replay_cli data/replay/opening-five-minutes.yaml
```

After installing the package, the equivalent entry point is:

```text
godzilla-replay data/replay/opening-five-minutes.yaml --config-dir config
```

The checked-in fixture contains five synthetic minute bars and one quote on the existing NSE
calendar. It produces one complete five-minute bar and ends `DISCONNECTED`. It is not a claim
about observed prices or a real vendor session. Repeating the command produces the same output
hash. Add recordings as new immutable files; preserve receipt times rather than assigning them
at replay time. Use `--calendar` to select the correct historical snapshot.

## Adapter integration contract

1. Capture exact vendor payloads with a stable ID, source and aware receipt time to `RawStorage`.
2. Decode to `Bar`/`Quote`; retain source flags and original event times. Reject malformed models.
3. Feed historical batches or serialized streaming callbacks through `MarketDataService`.
4. Schedule `window_health(instrument, source, start, end)` at expected closes, including when
   no callback arrives. Aggregate it with quote freshness and reconnect component health.
5. Read canonical bars via `canonical_bars()` and call `require_strategy_ready(at)` before any
   future feature/strategy consumer. Read execution quotes through `require_fresh`.
6. On disconnect call `disconnected(reason)`, honor bounded retry times, obtain fresh snapshots,
   reconcile gaps, and explicitly validate data recovery. Trading authorization remains separate.

`missing_bars` counts missing minute observations (stream window audits avoid recounting the
same missing key). `quality_violations` counts detected flags, `quote_age_seconds` records exchange
age, `provider_latency_seconds` records synchronous provider call duration, and `reconnects`
counts scheduled retry attempts. `MemoryMetrics` is a deterministic test sink; exporter binding
belongs to a later operational deployment.

## Scope and limitations

- Providers and durable stores remain replaceable interfaces. Only local recording and memory
  implementations are supplied; no vendor account, entitlement, network feed or orders are used.
- Raw vendor payload capture is the adapter's responsibility; the replay fixture is already
  normalized, not a substitute for a raw vendor archive.
- Session bars cover continuous trading only; pre-open observations are flagged out of session.
- No corporate-action adjustment is inferred. Abnormal changes remain flagged pending review.
- Flags fail closed for future strategy use, including zero volume and duplicate observations.
- Storage/network failures propagate to the application; durable recovery is Phase 4 work.

Run all quality gates with `make ci`. CI now includes unit, integration and replay tests.
