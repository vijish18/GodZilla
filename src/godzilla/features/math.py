"""Pure trailing numeric primitives with explicit sample requirements."""

import math
from statistics import fmean


def ratio_change(current: float, previous: float) -> float | None:
    return current / previous - 1 if previous > 0 else None


def population_std(values: tuple[float, ...]) -> float:
    mean = fmean(values)
    return math.sqrt(fmean((value - mean) ** 2 for value in values))


def covariance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("covariance requires equal nonempty windows")
    x, y = fmean(left), fmean(right)
    return fmean((a - x) * (b - y) for a, b in zip(left, right, strict=True))


def slope(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        raise ValueError("slope requires two observations")
    time = tuple(float(index) for index in range(len(values)))
    return covariance(time, values) / covariance(time, time)


def ema(values: tuple[float, ...], period: int) -> tuple[float, ...]:
    """SMA seed followed by alpha=2/(period+1); no pre-seed EMA is published."""
    if period < 1:
        raise ValueError("EMA period must be positive")
    if len(values) < period:
        return ()
    output = [fmean(values[:period])]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return tuple(output)
