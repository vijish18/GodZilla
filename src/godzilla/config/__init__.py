"""Typed, layered configuration with lazy public imports."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from godzilla.config.loader import ConfigLoadError, load_settings
    from godzilla.config.models import Settings
    from godzilla.config.modes import SystemState, TradingMode

__all__ = ["ConfigLoadError", "Settings", "SystemState", "TradingMode", "load_settings"]


def __getattr__(name: str) -> Any:
    if name in {"SystemState", "TradingMode"}:
        from godzilla.config import modes

        return getattr(modes, name)
    if name == "Settings":
        from godzilla.config.models import Settings

        return Settings
    if name in {"ConfigLoadError", "load_settings"}:
        from godzilla.config import loader

        return getattr(loader, name)
    raise AttributeError(name)
