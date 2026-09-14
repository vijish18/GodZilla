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
    assert capsys.readouterr().out.strip() == "0.1.0"


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
    assert cli._run(load_settings(CONFIG_DIR)) == 0
