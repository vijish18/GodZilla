"""Command-line entry points for foundation operations."""

from __future__ import annotations

import argparse
import json
import signal
from collections.abc import Sequence
from pathlib import Path
from threading import Event

from pydantic import ValidationError

from godzilla import __version__
from godzilla.app.lifecycle import LifecycleController
from godzilla.config.loader import ConfigLoadError, load_settings
from godzilla.config.models import Settings, TradingMode
from godzilla.core.clock import ProductionClock
from godzilla.core.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godzilla")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("version", help="print the application version")

    config_check = subcommands.add_parser("config-check", help="validate effective config")
    _add_config_arguments(config_check)

    run = subcommands.add_parser("run", help="run the lifecycle skeleton")
    _add_config_arguments(run)
    return parser


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--mode", type=lambda value: TradingMode(value.upper()))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0

    try:
        settings = load_settings(args.config_dir, args.mode)
    except (ConfigLoadError, ValidationError, ValueError) as exc:
        print(f"configuration invalid: {exc}")
        return 2

    if args.command == "config-check":
        print(json.dumps({"mode": settings.mode.value, "config_hash": settings.config_hash()}))
        return 0

    return _run(settings)


def _run(settings: Settings) -> int:
    logger = configure_logging(settings.logging.level, settings.logging.local_path)
    clock = ProductionClock()
    controller = LifecycleController(clock=clock)
    stopped = Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("shutdown signal received", extra={"signal": signum})
        controller.request_stop(f"signal:{signum}")
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    controller.mark_ready("foundation initialized")
    logger.info(
        "godzilla foundation ready",
        extra={"mode": settings.mode.value, "config_hash": settings.config_hash()},
    )
    stopped.wait()
    controller.mark_stopped("shutdown complete")
    logger.info("godzilla foundation stopped")
    return 0
