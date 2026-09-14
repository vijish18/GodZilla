import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.replay


def test_config_hash_is_stable_across_process_hash_seeds() -> None:
    root = Path(__file__).parents[2]
    hashes = []
    for seed in ("1", "2", "3"):
        environment = os.environ | {"PYTHONHASHSEED": seed}
        output = subprocess.run(
            [
                sys.executable,
                "-m",
                "godzilla",
                "config-check",
                "--config-dir",
                str(root / "config"),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        ).stdout
        hashes.append(json.loads(output)["config_hash"])
    assert len(set(hashes)) == 1
