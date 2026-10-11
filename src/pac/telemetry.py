"""Logging for pac: a logger per module, correlation ids on every line, text or JSON output.

How to log, anywhere in pac::

    logger = logging.getLogger(__name__)
    logger.info("classified", extra={"verdict": "violation", "latency_ms": 812})

Structured fields go in ``extra`` and become ``key=value`` pairs (text) or JSON keys. Correlation
ids live in a context variable, so every line logged while handling one API request, one eval
run, one case or one conversation turn carries the same ids without passing them through call
signatures::

    with log_context(run=run_id):
        ...  # every line below, including in tasks started here, carries run=<id>

Only the interfaces (CLI, API) call :func:`configure_logging`. Domain code doesn't log; it
returns values that say what happened.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, TextIO

LogFormat = Literal["text", "json"]

_context: ContextVar[Mapping[str, str]] = ContextVar(
    "pac_log_context", default=MappingProxyType({})
)

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "context",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured copy of the message
}

# Correlation ids, in the order they appear on a text line.
_ID_ORDER = ("request", "run", "case", "turn")


def new_id() -> str:
    """A short id for correlating log lines (8 hex characters)."""
    return uuid.uuid4().hex[:8]


@contextmanager
def log_context(**ids: str) -> Iterator[None]:
    """Attach correlation ids to every log line inside the block, including in tasks it starts."""
    token = _context.set(MappingProxyType({**_context.get(), **ids}))
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> Mapping[str, str]:
    return _context.get()


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.context = dict(_context.get())
        return True


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")}


def _ordered_ids(context: Mapping[str, str]) -> list[tuple[str, str]]:
    known = [(k, context[k]) for k in _ID_ORDER if k in context]
    return known + sorted((k, v) for k, v in context.items() if k not in _ID_ORDER)


def _text(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"  # 0.6122 rather than 0.6122448979591837
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return f'"{text}"' if isinstance(value, str) and (" " in text or not text) else text


# uvicorn logs its own status ("Started server process", …) to "uvicorn.error", which reads as a
# failure in a log line. Nothing failed, so text logs call it "uvicorn".
_DISPLAY_NAMES = {"uvicorn.error": "uvicorn"}


class TextFormatter(logging.Formatter):
    """``16:40:01.123 INFO    guardrail.llm_classifier [run=… case=…] message key=value …``"""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).astimezone()
        name = _DISPLAY_NAMES.get(record.name) or (
            record.name.removeprefix("pac.")
            .replace(".application", "")
            .replace(".infrastructure", "")
        )
        context = getattr(record, "context", {})
        ids = " ".join(f"{k}={v}" for k, v in _ordered_ids(context))
        fields = " ".join(f"{k}={_text(v)}" for k, v in _fields(record).items())
        line = (
            f"{stamp:%H:%M:%S}.{stamp.microsecond // 1000:03d} {record.levelname:<7} {name}"
            + (f" [{ids}]" if ids else "")
            + f" {record.getMessage()}"
            + (f" {fields}" if fields else "")
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, ids and fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "context", {}),
            **_fields(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    level: str = "INFO", fmt: LogFormat = "text", stream: TextIO | None = None
) -> None:
    """Send pac's logs, and the web server's, to ``stream`` (stderr) in one format.

    Idempotent: calling it again replaces the handler instead of adding a second one.
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(_ContextFilter())
    # Parents only: their children (pac.*, uvicorn.error) propagate up to these handlers.
    for name in ("pac", "uvicorn"):
        logger = logging.getLogger(name)
        for old in list(logger.handlers):
            if getattr(old, "_pac", False):
                logger.removeHandler(old)
        handler._pac = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(level.upper())
    # The HTTP clients log every request at INFO; pac logs the calls that matter itself.
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
