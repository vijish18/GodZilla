"""Tie-aware mid-distribution ranks: invariant to input order and monotonic rescaling."""

import math


def percentiles(values: tuple[float, ...]) -> tuple[float, ...]:
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("ranks require finite, nonempty observations")
    return tuple(
        (sum(other < value for other in values) + 0.5 * sum(other == value for other in values))
        / len(values)
        for value in values
    )


def without_recent(total_return: float, recent_return: float) -> float:
    if min(total_return, recent_return) <= -1:
        raise ValueError("price returns must exceed -1")
    return (1 + total_return) / (1 + recent_return) - 1
