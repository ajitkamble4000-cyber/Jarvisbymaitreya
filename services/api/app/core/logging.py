"""Structured (JSON) logging with secret redaction and an in-memory ring buffer.

The ring buffer backs the Developer Console. Records are redacted before they are
stored or printed, so API keys, bearer tokens and passwords never reach logs.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Any

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"hf_[A-Za-z0-9]{16,}"), "[REDACTED_HF_TOKEN]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/=]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|x-goog-api-key|cookie)(['\"]?\s*[:=]\s*['\"]?)([^'\"\s,;}&]+)"),
        r"\1\2[REDACTED]",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    _STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return redact(json.dumps(payload, default=str))


class RingBufferHandler(logging.Handler):
    """Keeps the most recent log records in memory for the Developer Console."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key in ("request_id", "task_id", "device_id", "provider_id"):
            if hasattr(record, key):
                entry[key] = str(getattr(record, key))
        with self._lock:
            self._records.append(entry)

    def recent(self, limit: int = 200, min_level: int = logging.DEBUG) -> list[dict[str, Any]]:
        with self._lock:
            items = [r for r in self._records if logging.getLevelName(r["level"]) >= min_level]
        return items[-limit:]


ring_buffer = RingBufferHandler()


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter())
    stream.addFilter(RedactingFilter())
    root.addHandler(stream)

    ring_buffer.addFilter(RedactingFilter())
    root.addHandler(ring_buffer)

    # uvicorn's access log duplicates request logging; the app logs requests itself.
    logging.getLogger("uvicorn.access").disabled = True
