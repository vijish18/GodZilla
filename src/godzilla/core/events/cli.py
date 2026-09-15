"""Offline event replay smoke command."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from godzilla.core.clock import ReplayClock
from godzilla.core.events.replay import ReplayEngine, read_fixture
from godzilla.core.events.serialization import serialize_event


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="godzilla-event-replay")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    events = read_fixture(args.fixture)
    if not events:
        raise ValueError("event fixture is empty")
    clock = ReplayClock(events[0].received_at)
    serialized: list[str] = []
    count = ReplayEngine(clock).run(events, lambda event: serialized.append(serialize_event(event)))
    print(
        json.dumps(
            {
                "events": count,
                "final_clock": clock.now().isoformat(),
                "output_hash": hashlib.sha256("\n".join(serialized).encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
