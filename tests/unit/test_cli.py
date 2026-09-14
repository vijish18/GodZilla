import json
from pathlib import Path

import pytest

from godzilla.app import cli
from godzilla.app.cli import main
from godzilla.config.loader import load_settings

pytestmark = pytest.mark.unit
CONFIG_DIR = Path(__file__).parents[2] / "config"


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.2.0"


def test_config_check_reports_mode_and_hash(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config-check", "--config-dir", str(CONFIG_DIR)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "PAPER"
    assert len(result["config_hash"]) == 64


def test_config_check_fails_for_live(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config-check", "--config-dir", str(CONFIG_DIR), "--mode", "live"]) == 2
    assert "LIVE activation blocked" in capsys.readouterr().out


def test_run_handles_a_shutdown_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers = {}

    class ImmediateSignalEvent:
        def set(self) -> None:
            pass

        def wait(self) -> None:
            handlers[cli.signal.SIGTERM](cli.signal.SIGTERM, None)

    def capture_handler(signum: int, handler: object) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(cli, "Event", ImmediateSignalEvent)
    monkeypatch.setattr(cli.signal, "signal", capture_handler)
    from datetime import UTC, datetime

    from godzilla.core.clock import FixedClock

    clock = FixedClock(datetime(2026, 9, 15, tzinfo=UTC))
    assert cli._run(load_settings(CONFIG_DIR), clock) == 0


def test_snapshot_check_uses_local_fixtures(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(__file__).parents[2]
    assert (
        main(
            [
                "snapshot-check",
                "--config-dir",
                str(CONFIG_DIR),
                "--calendar",
                str(root / "data/calendar/nse-cm-2026.yaml"),
                "--instruments",
                str(root / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"),
                "--at",
                "2026-09-15T09:30:00+05:30",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["session_state"] == "OPEN"
    assert result["source"] == "local-fixtures-only"
