"""Operating modes and process lifecycle states."""

from enum import StrEnum


class TradingMode(StrEnum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class SystemState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"
    EMERGENCY = "EMERGENCY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
