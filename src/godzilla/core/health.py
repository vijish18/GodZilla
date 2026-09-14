"""Generic component and aggregate health primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from types import MappingProxyType
from typing import Any


class HealthStatus(IntEnum):
    HEALTHY = 0
    UNKNOWN = 1
    DEGRADED = 2
    UNHEALTHY = 3


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: str
    status: HealthStatus
    observed_at: datetime
    message: str = ""
    blocking: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("health observation timestamp must be timezone-aware")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class SystemHealth:
    status: HealthStatus
    components: tuple[ComponentHealth, ...]
    blocks_new_entries: bool

    @classmethod
    def aggregate(cls, components: Iterable[ComponentHealth]) -> SystemHealth:
        collected = tuple(components)
        status = max((item.status for item in collected), default=HealthStatus.UNKNOWN)
        blocked = any(
            item.blocking and item.status is not HealthStatus.HEALTHY for item in collected
        )
        return cls(status=status, components=collected, blocks_new_entries=blocked)
