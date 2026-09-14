"""Structured JSON logging with defensive secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from godzilla.core.ids import CorrelationId

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"(?:password|secret|token|api[_-]?key|authorization)", re.IGNORECASE)
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)\b\s*([:=])\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^,\s}]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_STANDARD_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        cleaned = _BEARER.sub(f"Bearer {REDACTED}", value)
        return _INLINE_SECRET.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", cleaned
        )
    return value


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message: Any
        if isinstance(record.msg, (Mapping, list, tuple)):
            message = redact(record.msg)
        else:
            message = redact(record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        correlation = getattr(record, "correlation_id", None) or _correlation_id.get()
        if correlation is not None:
            payload["correlation_id"] = str(correlation)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_KEYS
            and key not in {"message", "asctime", "correlation_id"}
        }
        if extras:
            payload["fields"] = redact(extras)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(
    level: str = "INFO", local_path: Path | None = None, stream: TextIO | None = None
) -> logging.Logger:
    logger = logging.getLogger("godzilla")
    logger.setLevel(level.upper())
    logger.propagate = False
    logger.handlers.clear()
    formatter = RedactingJsonFormatter()

    stream_handler = logging.StreamHandler(stream or sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if local_path is not None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(local_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


@contextmanager
def correlation_context(correlation_id: CorrelationId | str) -> Iterator[None]:
    token: Token[str | None] = _correlation_id.set(str(correlation_id))
    try:
        yield
    finally:
        _correlation_id.reset(token)
