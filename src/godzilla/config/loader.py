"""Deterministic YAML layering with environment overrides."""

from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from godzilla.config.models import Settings, TradingMode


class ConfigLoadError(RuntimeError):
    """Raised when a configuration layer cannot be read safely."""


_PROFILE_BY_MODE = {
    TradingMode.RESEARCH: "research.yaml",
    TradingMode.BACKTEST: "research.yaml",
    TradingMode.PAPER: "paper.yaml",
    TradingMode.LIVE: "production.yaml",
}


def load_settings(config_dir: Path | str, mode: TradingMode | None = None) -> Settings:
    directory = Path(config_dir)
    base = _load_yaml(directory / "base.yaml")
    selected_mode = mode or _mode_from_environment() or _mode_from_mapping(base)
    merged = _deep_merge(base, _load_yaml(directory / _PROFILE_BY_MODE[selected_mode]))
    merged = _deep_merge(merged, _load_yaml(directory / "compliance.yaml"))
    merged["mode"] = selected_mode.value
    return Settings(**merged)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            content = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigLoadError(f"unable to load {path}: {exc}") from exc
    if not isinstance(content, dict):
        raise ConfigLoadError(f"configuration layer must be a mapping: {path}")
    return content


def _mode_from_environment() -> TradingMode | None:
    value = os.getenv("GODZILLA_MODE")
    if value is None:
        return None
    try:
        return TradingMode(value.upper())
    except ValueError as exc:
        raise ConfigLoadError(f"invalid GODZILLA_MODE: {value}") from exc


def _mode_from_mapping(config: Mapping[str, Any]) -> TradingMode:
    value = config.get("mode", TradingMode.PAPER.value)
    try:
        return TradingMode(str(value).upper())
    except ValueError as exc:
        raise ConfigLoadError(f"invalid base mode: {value}") from exc


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
