import json
from io import StringIO
from pathlib import Path

import pytest

from godzilla.core.ids import CorrelationId
from godzilla.core.logging import REDACTED, configure_logging, correlation_context

pytestmark = pytest.mark.unit


def test_structured_logging_redacts_nested_secrets_and_adds_correlation() -> None:
    stream = StringIO()
    logger = configure_logging(stream=stream)
    correlation_id = CorrelationId.new()

    with correlation_context(correlation_id):
        logger.info(
            "request authorization=Bearer-secret",
            extra={
                "payload": {
                    "password": "do-not-log",
                    "nested": {"api_key": "also-secret", "symbol": "SBIN"},
                }
            },
        )

    payload = json.loads(stream.getvalue())
    serialized = json.dumps(payload)
    assert payload["correlation_id"] == str(correlation_id)
    assert payload["fields"]["payload"]["password"] == REDACTED
    assert payload["fields"]["payload"]["nested"]["api_key"] == REDACTED
    assert "do-not-log" not in serialized
    assert "also-secret" not in serialized
    assert "Bearer-secret" not in serialized


def test_mapping_messages_are_redacted() -> None:
    stream = StringIO()
    logger = configure_logging(stream=stream)
    logger.info({"token": "hidden", "event": "startup"})
    payload = json.loads(stream.getvalue())
    assert payload["message"] == {"token": REDACTED, "event": "startup"}


def test_local_json_logging(tmp_path: Path) -> None:
    log_path = tmp_path / "godzilla.jsonl"
    logger = configure_logging(local_path=log_path, stream=StringIO())
    logger.warning("local event", extra={"secret": "hidden"})

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["level"] == "WARNING"
    assert payload["fields"]["secret"] == REDACTED
