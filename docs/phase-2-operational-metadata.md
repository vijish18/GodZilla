# Phase 2 operational metadata

The checked-in calendar follows the NSE Capital Market 2026 holiday circular and regular cash
market timings. The announced 8 November 2026 Muhurat session is marked `TIMINGS_PENDING`, so it
remains closed until NSE publishes confirmed times and a reviewed snapshot replaces this one.

The compliance profile records the India retail-algo framework effective 1 April 2026, its
10-orders-per-second threshold, static-IP and tagging concepts, and primary sources. It describes
only `LOCAL_PAPER_SIMULATOR`; `live_allowed` is false. Broker order types, short permissions,
daily API-session reset behavior, static IP registration, and tagging must be verified in a
broker-specific profile before LIVE can validate.

The instrument and mapping files are explicitly synthetic deterministic fixtures. They must not
be treated as current NSE or broker reference data. A production provider should write a new
immutable snapshot, retain the raw source reference and generation time, and receive independent
review before selection.

Run the local operational check with:

```bash
godzilla snapshot-check --config-dir config
```

The command performs no network requests. `--at` accepts an ISO 8601 timestamp with an offset for
deterministic incident checks and tests.
