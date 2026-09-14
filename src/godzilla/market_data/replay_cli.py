"""Run a recorded session through the same ingestion path as streaming adapters."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from godzilla import __version__
from godzilla.config.loader import load_settings
from godzilla.core.clock import FixedClock
from godzilla.market_data.calendar import NseExchangeCalendar
from godzilla.market_data.freshness import QuoteFreshnessTracker
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_data.providers import LocalCalendarProvider, load_yaml_model
from godzilla.market_data.quality import BarQualityChecker
from godzilla.market_data.reconnect import ExponentialBackoff, ReconnectController
from godzilla.market_data.replay import RecordedProvider, ReplayRecording
from godzilla.market_data.service import MarketDataService
from godzilla.market_data.storage import MemoryNormalizedStorage


def run_recording(path: Path, calendar_path: Path, config_dir: Path) -> dict[str, object]:
    recording = load_yaml_model(path, ReplayRecording)
    events = (*recording.bars, *recording.quotes)
    if not events:
        raise ValueError("recording is empty")
    clock = FixedClock(min(event.received_at for event in events))
    settings = load_settings(config_dir)
    metrics = MemoryMetrics()
    tracker = QuoteFreshnessTracker(clock, settings.execution.quote_stale_seconds, metrics)
    checker = BarQualityChecker(
        NseExchangeCalendar(LocalCalendarProvider(calendar_path).load()),
        settings.data_quality,
        settings.market.timezone,
    )
    provider = RecordedProvider(recording, clock)
    service = MarketDataService(
        provider, clock, checker, MemoryNormalizedStorage(), tracker, metrics
    )
    reconnect = ReconnectController(clock, ExponentialBackoff(), metrics)
    instruments = tuple(sorted({event.instrument_id for event in events}))
    provider.subscribe(
        instruments, service.ingest_bar, service.ingest_quote, reconnect.disconnected
    )
    bars = service.canonical_bars()
    payload = [json.loads(bar.model_dump_json()) for bar in bars]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {
        "software_version": __version__,
        "recording_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "calendar_hash": hashlib.sha256(calendar_path.read_bytes()).hexdigest(),
        "config_hash": settings.config_hash(),
        "normalization_version": settings.data_quality.normalization_version,
        "recording": recording.snapshot_id,
        "canonical_bars": len(bars),
        "complete_bars": sum(bar.complete and not bar.quality_flags for bar in bars),
        "output_hash": digest,
        "stream_state": reconnect.state.value,
        "metrics": metrics.counters,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="godzilla-replay")
    parser.add_argument("recording", type=Path)
    parser.add_argument("--calendar", type=Path, default=Path("data/calendar/nse-cm-2026.yaml"))
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    args = parser.parse_args(argv)
    print(json.dumps(run_recording(args.recording, args.calendar, args.config_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
