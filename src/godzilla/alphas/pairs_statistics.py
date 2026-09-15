"""OLS baseline and replaceable formal diagnostics, restricted to training observations."""

import math
import warnings
from importlib.metadata import version
from statistics import fmean
from typing import Protocol

from godzilla.alphas.pairs_models import PairFit, PairObservation, PairsSettings, StatisticalTests
from godzilla.features.math import covariance, population_std
from godzilla.features.models import FeatureModel, content_hash


class PairDiagnostics(Protocol):
    def test(
        self,
        left: tuple[float, ...],
        right: tuple[float, ...],
        spread: tuple[float, ...],
        lags: int,
    ) -> StatisticalTests: ...


class StatsmodelsDiagnostics:
    def test(
        self,
        left: tuple[float, ...],
        right: tuple[float, ...],
        spread: tuple[float, ...],
        lags: int,
    ) -> StatisticalTests:
        # No stubs upstream: isolate the untyped library at this checked boundary.
        from statsmodels.tsa.stattools import adfuller, coint  # type: ignore[import-untyped]

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return StatisticalTests(
                method_version="aeg-c-adf-c-fixed-lags;"
                + ";".join(
                    name + "=" + version(name)
                    for name in ("statsmodels", "numpy", "scipy", "pandas")
                ),
                cointegration_p=float(coint(left, right, trend="c", maxlag=lags, autolag=None)[1]),
                spread_adf_p=float(adfuller(spread, maxlag=lags, regression="c", autolag=None)[1]),
                left_level_adf_p=float(
                    adfuller(left, maxlag=lags, regression="c", autolag=None)[1]
                ),
                right_level_adf_p=float(
                    adfuller(right, maxlag=lags, regression="c", autolag=None)[1]
                ),
            )


class _Training(FeatureModel):
    observations: tuple[PairObservation, ...]


def ols(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, float]:
    if len(left) != len(right) or len(left) < 3:
        raise ValueError("insufficient aligned OLS observations")
    variance = covariance(right, right)
    if variance <= 1e-15:
        raise ValueError("degenerate regressor")
    beta = covariance(left, right) / variance
    return fmean(left) - beta * fmean(right), beta


def fit_pair(
    training: tuple[PairObservation, ...], settings: PairsSettings, diagnostics: PairDiagnostics
) -> PairFit:
    if len(training) != settings.training_bars:
        raise ValueError("training window length mismatch")
    left = tuple(math.log(float(o.left.close)) for o in training)
    right = tuple(math.log(float(o.right.close)) for o in training)
    intercept, beta = ols(left, right)
    if beta <= 0:
        raise ValueError("nonpositive hedge ratio")
    spread = tuple(a - beta * b for a, b in zip(left, right, strict=True))
    std = population_std(spread)
    if std <= settings.min_spread_std:
        raise ValueError("degenerate spread variance")
    middle = len(training) // 2
    beta_a, beta_b = ols(left[:middle], right[:middle])[1], ols(left[middle:], right[middle:])[1]
    std_a, std_b = population_std(spread[:middle]), population_std(spread[middle:])
    if min(std_a, std_b) <= settings.min_spread_std:
        raise ValueError("degenerate split spread variance")
    _, phi = ols(spread[1:], spread[:-1])
    half_life = -math.log(2) / math.log(phi) if 0 < phi < 1 else None
    return PairFit(
        beta=beta,
        intercept=intercept,
        spread_mean=fmean(spread),
        spread_std=std,
        half_life_bars=half_life,
        split_beta_drift=abs(beta_a - beta_b) / abs(beta),
        spread_std_ratio=max(std_a, std_b) / min(std_a, std_b),
        spread_mean_shift=abs(fmean(spread[:middle]) - fmean(spread[middle:])) / std,
        tests=diagnostics.test(left, right, spread, settings.test_lags),
        training_start=training[0].left.start,
        training_end=training[-1].left.end,
        training_hash=content_hash(_Training(observations=training)),
        observations=len(training),
    )


def relationship_rejections(fit: PairFit, cfg: PairsSettings) -> tuple[str, ...]:
    tests = fit.tests
    checks = {
        "COINTEGRATION_REJECTED": tests.cointegration_p >= cfg.significance,
        "SPREAD_STATIONARITY_REJECTED": tests.spread_adf_p >= cfg.significance,
        "I1_ASSUMPTION_REJECTED": min(tests.left_level_adf_p, tests.right_level_adf_p)
        < cfg.significance,
        "BETA_OUT_OF_RANGE": not cfg.min_beta <= fit.beta <= cfg.max_beta,
        "UNSTABLE_BETA": fit.split_beta_drift > cfg.max_beta_drift,
        "UNSTABLE_SPREAD_VARIANCE": fit.spread_std_ratio > cfg.max_spread_std_ratio,
        "UNSTABLE_SPREAD_MEAN": fit.spread_mean_shift > cfg.max_spread_mean_shift,
        "HALF_LIFE_REJECTED": fit.half_life_bars is None
        or not cfg.min_half_life_bars <= fit.half_life_bars <= cfg.max_half_life_bars,
        "NEUTRALITY_LIMIT": abs(1 - fit.beta) / (1 + fit.beta) > cfg.max_net_gross_fraction,
    }
    return tuple(reason for reason, rejected in checks.items() if rejected)


def spread_z(observation: PairObservation, fit: PairFit) -> tuple[float, float]:
    spread = math.log(float(observation.left.close)) - fit.beta * math.log(
        float(observation.right.close)
    )
    return spread, (spread - fit.spread_mean) / fit.spread_std
