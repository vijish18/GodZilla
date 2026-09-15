# Phase 5: causal feature contract

`CausalFeatureEngine.on_event()` ingests immutable events; `snapshot(context, at)`
evaluates only available inputs. `batch_snapshot()` uses that identical path. A
`FeatureContext` specifies instrument/source identities, benchmark and pair references,
membership/universe version, known-at and effective timestamps, and optional proposed
quantity for participation estimates. Quantity is an input, not a sizing decision.

Use `publish_snapshot(journal, snapshot, definition, correlation_id)` for durable
publication, then `FeatureRepository.get_feature_snapshot(hash)` for retrieval.
Apply `alembic upgrade head` before using persistence. All tests use synthetic local
fixtures; `python scripts/test_postgres.py` additionally exercises an isolated database.

## Availability and warm-up

Snapshots include only envelope receipts at or before evaluation time. Bars must also
be fully closed and received, canonical, aligned and contiguous from session open.
The session calendar supplies regular and special-session boundaries. No centered
window, forward fill or completed-session total is available early. All intraday
windows reset at session open, including EMA and ATR. A missing interval invalidates
that instrument's current session series until it becomes available. A later receipt
cannot repair an earlier snapshot. Duplicate bar timestamps invalidate the series.

Values are finite floats or `None` with `WARMUP`, `MISSING_INPUT`, `INVALID_INPUT`,
`STALE`, `ZERO_DENOMINATOR` or `NOT_IMPLEMENTED`. Dependent relative features may use
the broader `MISSING_INPUT` reason. Consumers must require every needed value; this
phase does not turn missing features into a trade permission.

## Formula catalog

The following formulas apply to stock, NIFTY (`market`) and sector price series.
Prices refer to completed five-minute closes. All returns are fractions, not percent.

- Returns at 5/15/30/60/120 minutes: `close[t]/close[t-h/5]-1`, requiring `h/5+1`
  session bars. Slope uses OLS of those closes on bar index, divided by first close;
  units are fractional price per five-minute step.
- EMA 9/20: SMA seed after 9/20 bars, then alpha `2/(period+1)`. Slope is fractional
  change of successive EMA values and needs one additional bar.
- Session VWAP: cumulative `typical_price * volume / volume`, where typical price
  is `(high+low+close)/3`. Distance is `close/VWAP-1`. These are OHLCV estimates,
  not exchange trade-level VWAP. Zero denominator is unavailable.
- Opening gap: first session open / immediately preceding exchange session close
  minus one. That preceding session must be complete; older sessions cannot substitute.
- Realized volatility: population standard deviation of the last `statistics_bars`
  log returns (default 20; 21 closes). No annualization.
- Range volatility: root mean square of `(high-low)/close` over the last 20 bars.
- Range percentile: fraction of the previous 20 `(high-low)/close` ranges less than
  or equal to the current range (21 bars including current).
- ATR: simple mean of the last 14 true ranges using previous close (15 bars).
  This is explicitly a simple ATR, not Wilder smoothing.
- Opening range: high/low of first 3 bars, published only after all three close.
- Swing points: strict local extrema with 2 bars on each side; published only after
  the two confirming bars arrive. Ties do not qualify. Never backdated.
- Relative strength: stock minus sector, stock minus NIFTY and sector minus NIFTY
  returns at each horizon. These are benchmark-relative return residuals, not fitted
  factor-model residuals.

Market and sector breadth use one-bar returns of configured point-in-time members.
Coverage is observed/expected membership; advancing/declining fractions require full
coverage. India VIX level is the latest close and change is the one-bar fractional
change. Missing context feeds remain unavailable. Price-only index/VIX bars may have
zero volume; they cannot provide VWAP.

Liquidity spread is `(ask-bid)/mid*10000`; quote age is seconds. Quotes must be
quality-cleared, positive, uncrossed and no more than 3 seconds old. Median spread
uses valid observations in the last 300 seconds and requires a fresh latest quote.
ADV and estimated daily turnover average the immediately preceding 20 exchange
sessions, all complete. Turnover uses typical price times volume. Participation is
proposed quantity / ADV or proposed quantity times quote mid / daily turnover.
Relative volume/turnover compare the latest bar against the same session slot in
those prior sessions. No current-session total is extrapolated. Calendar lookback is
bounded to 366 dates; insufficient coverage remains unavailable.

Pair primitives use aligned positive log closes. The previous 20 observations,
excluding current, estimate population covariance and right-leg variance, with
hedge ratio covariance/variance. Using that fixed ratio, compute training spread
mean/std and current spread `log(left)-beta*log(right)`. Z-score is current deviation
from training mean divided by training std; std at or below numerical tolerance
`1e-12` is unavailable. Log correlation uses the same training window. These are
inputs for research, not a validated pair relationship. Stationarity p-value and
half-life explicitly return `NOT_IMPLEMENTED`.

The version hash covers parameter choices; formula revision 1 fixes the formulas and
numeric policy above. Code commit additionally identifies the exact implementation.
Any formula change requires a new revision. Snapshot hashes cover values, missing
reasons, context/input hashes and provenance. Inputs must be retained in the event
archive identified by provenance for reproduction.
