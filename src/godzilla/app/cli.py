"""Command-line entry points for local validation and lifecycle operations."""

from __future__ import annotations

import argparse
import json
import signal
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from threading import Event

from pydantic import ValidationError

from godzilla import __version__
from godzilla.app.lifecycle import LifecycleController
from godzilla.compliance.service import ComplianceGate, ComplianceViolation
from godzilla.config.loader import ConfigLoadError, load_settings
from godzilla.config.models import Settings
from godzilla.config.modes import TradingMode
from godzilla.core.clock import Clock, FixedClock, ProductionClock
from godzilla.core.logging import configure_logging
from godzilla.market_data.calendar import NseExchangeCalendar
from godzilla.market_data.instruments import LocalInstrumentProvider
from godzilla.market_data.providers import LocalCalendarProvider, SnapshotLoadError
from godzilla.universe.builder import UniverseBuilder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godzilla")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("version", help="print the application version")
    config_check = subcommands.add_parser("config-check", help="validate effective config")
    _add_config_arguments(config_check)
    run = subcommands.add_parser("run", help="run the lifecycle skeleton")
    _add_config_arguments(run)
    snapshot = subcommands.add_parser(
        "snapshot-check", help="validate local calendar, instrument and compliance snapshots"
    )
    _add_config_arguments(snapshot)
    snapshot.add_argument("--calendar", type=Path, default=Path("data/calendar/nse-cm-2026.yaml"))
    snapshot.add_argument(
        "--instruments",
        type=Path,
        default=Path("data/instruments/nse-cm-synthetic-2026-09-15.yaml"),
    )
    snapshot.add_argument(
        "--at", type=datetime.fromisoformat, help="aware instant; current UTC time when omitted"
    )
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
        if args.command == "config-check":
            print(json.dumps({"mode": settings.mode.value, "config_hash": settings.config_hash()}))
            return 0
        if args.command == "snapshot-check":
            return _snapshot_check(settings, args.calendar, args.instruments, args.at)
        return _run(settings)
    except (
        ComplianceViolation,
        ConfigLoadError,
        SnapshotLoadError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"validation failed: {exc}")
        return 2


def _run(settings: Settings, clock: Clock | None = None) -> int:
    logger = configure_logging(settings.logging.level, settings.logging.local_path)
    runtime_clock = clock or ProductionClock()
    ComplianceGate(runtime_clock).validate(settings.compliance, settings.mode)
    controller = LifecycleController(clock=runtime_clock)
    stopped = Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("shutdown signal received", extra={"signal": signum})
        controller.request_stop(f"signal:{signum}")
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    controller.mark_ready("foundation and compliance checks complete")
    logger.info(
        "godzilla foundation ready",
        extra={"mode": settings.mode.value, "config_hash": settings.config_hash()},
    )
    stopped.wait()
    controller.mark_stopped("shutdown complete")
    logger.info("godzilla foundation stopped")
    return 0


def _snapshot_check(
    settings: Settings,
    calendar_path: Path,
    instrument_path: Path,
    instant: datetime | None,
) -> int:
    at = instant or ProductionClock().now()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("--at must include a UTC offset")
    ComplianceGate(FixedClock(at)).validate(settings.compliance, settings.mode)
    calendar = NseExchangeCalendar(LocalCalendarProvider(calendar_path).load())
    master = LocalInstrumentProvider(instrument_path).load()
    universe = UniverseBuilder().build(
        master=master,
        compliance=settings.compliance,
        settings=settings.universe,
        as_of=calendar.local_date(at),
        generated_at=at,
    )
    result = {
        "calendar_version": calendar.snapshot.version,
        "compliance_version": settings.compliance.version,
        "instrument_version": master.version,
        "session_state": calendar.state_at(at).value,
        "universe_snapshot_id": str(universe.snapshot_id),
        "long_count": len(universe.long_tokens),
        "short_count": len(universe.short_tokens),
        "source": "local-fixtures-only",
    }
    print(json.dumps(result, sort_keys=True))
    return 0
