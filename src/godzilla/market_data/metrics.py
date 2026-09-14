"""Injectable metrics with an in-memory implementation for deterministic tests."""

from dataclasses import dataclass, field
from typing import Protocol


class Metrics(Protocol):
    def increment(self, name: str, value: int = 1) -> None: ...

    def observe(self, name: str, value: float) -> None: ...


@dataclass
class MemoryMetrics:
    counters: dict[str, int] = field(default_factory=dict)
    observations: dict[str, list[float]] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, value: float) -> None:
        self.observations.setdefault(name, []).append(value)
