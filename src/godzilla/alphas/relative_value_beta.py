"""Training-only NIFTY return beta; no current decision bar or overnight return."""

from datetime import datetime, timedelta
from itertools import pairwise

from godzilla.alphas.pairs_statistics import ols
from godzilla.alphas.relative_value_models import (
    BetaEstimate,
    BetaObservation,
    RelativeValueSettings,
)
from godzilla.features.models import FeatureModel, InstrumentRef, content_hash
from godzilla.market_data.models import Bar, FeedKind, utc


class _Training(FeatureModel):
    observations: tuple[BetaObservation, ...]


class _MarketTraining(FeatureModel):
    bars: tuple[Bar, ...]


def estimate_beta(
    history: tuple[BetaObservation, ...],
    stock: InstrumentRef,
    market: InstrumentRef,
    at: datetime,
    opening: datetime,
    cfg: RelativeValueSettings,
) -> BetaEstimate:
    at, opening = utc(at), utc(opening)
    cutoff = at - timedelta(minutes=5)
    rows = tuple(
        sorted(
            (
                row
                for row in history
                if row.stock.start >= opening
                and max(
                    row.stock.end, row.market.end, row.stock.received_at, row.market.received_at
                )
                <= cutoff
            ),
            key=lambda row: row.stock.end,
        )
    )[-cfg.training_returns - 1 :]
    if len(rows) != cfg.training_returns + 1:
        raise ValueError("BETA_WARMUP")
    if rows[-1].stock.end != cutoff:
        raise ValueError("BETA_HISTORY_STALE")
    for index, row in enumerate(rows):
        for bar, ref, kind in (
            (row.stock, stock, FeedKind.EQUITY),
            (row.market, market, FeedKind.INDEX),
        ):
            bar.require_strategy_ready(cutoff)
            if (
                bar.instrument_id != ref.instrument_id
                or bar.source != ref.source
                or bar.feed_kind is not kind
            ):
                raise ValueError("BETA_FEED_MISMATCH")
        if (
            row.stock.start != row.market.start
            or row.stock.end != row.market.end
            or (row.stock.start - opening).total_seconds() % 300 != 0
            or (index and rows[index - 1].stock.end != row.stock.start)
        ):
            raise ValueError("BETA_HISTORY_GAP_OR_DUPLICATE")
    left = tuple(float(b.stock.close / a.stock.close) - 1 for a, b in pairwise(rows))
    right = tuple(float(b.market.close / a.market.close) - 1 for a, b in pairwise(rows))
    intercept, beta = ols(left, right)
    if not cfg.min_beta <= beta <= cfg.max_beta:
        raise ValueError("BETA_OUT_OF_RANGE")
    half = len(left) // 2
    first, second = ols(left[:half], right[:half])[1], ols(left[half:], right[half:])[1]
    drift = abs(first - second) / beta
    if drift > cfg.max_split_beta_drift:
        raise ValueError("BETA_UNSTABLE")
    return BetaEstimate(
        beta=beta,
        intercept=intercept,
        split_drift=drift,
        training_start=rows[0].stock.start,
        training_end=rows[-1].stock.end,
        returns=len(left),
        input_hash=content_hash(_Training(observations=rows)),
        market_hash=content_hash(_MarketTraining(bars=tuple(row.market for row in rows))),
    )


def neutral_weights(
    long_beta: float, short_beta: float, cfg: RelativeValueSettings
) -> tuple[float, float]:
    """Unit gross, exact fitted beta neutrality, rejected if net exposure is infeasible."""
    if not cfg.min_beta <= min(long_beta, short_beta) <= max(long_beta, short_beta) <= cfg.max_beta:
        raise ValueError("BETA_OUT_OF_RANGE")
    long_weight = short_beta / (long_beta + short_beta)
    short_weight = 1 - long_weight
    if abs(long_weight - short_weight) > cfg.max_net_gross + 1e-12:
        raise ValueError("NET_EXPOSURE_INFEASIBLE")
    if abs(long_weight * long_beta - short_weight * short_beta) > cfg.max_beta_gross + 1e-12:
        raise ValueError("BETA_EXPOSURE_INFEASIBLE")
    return long_weight, short_weight
