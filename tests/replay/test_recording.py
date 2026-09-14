from pathlib import Path

import pytest

from godzilla.market_data.replay_cli import main, run_recording

pytestmark = pytest.mark.replay
ROOT = Path(__file__).parents[2]


def test_recording_is_repeatable_and_ends_disconnected(capsys):
    path = ROOT / "data/replay/opening-five-minutes.yaml"
    calendar = ROOT / "data/calendar/nse-cm-2026.yaml"
    config = ROOT / "config"
    first = run_recording(path, calendar, config)
    assert first == run_recording(path, calendar, config)
    assert first["complete_bars"] == 1
    assert first["stream_state"] == "DISCONNECTED"
    assert len(first["output_hash"]) == 64
    assert main([str(path), "--calendar", str(calendar), "--config-dir", str(config)]) == 0
    assert "output_hash" in capsys.readouterr().out
