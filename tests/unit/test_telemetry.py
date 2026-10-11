"""Logging: correlation ids on every line, readable text or JSON, configured once."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Iterator

import pytest

from pac.telemetry import configure_logging, current_context, log_context, new_id

LOGGER = logging.getLogger("pac.guardrail.application.llm_classifier")


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    yield buffer
    configure_logging("WARNING", "text", io.StringIO())  # don't leak our stream into later tests


def test_text_lines_carry_ids_and_fields(stream: io.StringIO) -> None:
    configure_logging("DEBUG", "text", stream)
    with log_context(run="r1", case="c-7"), log_context(request="q9"):
        LOGGER.info(
            "classified",
            extra={"verdict": "violation", "reason": "two words", "accuracy": 0.6122448979591837},
        )
    line = stream.getvalue().strip()
    assert " INFO    guardrail.llm_classifier [request=q9 run=r1 case=c-7] classified " in line
    assert line.endswith('verdict=violation reason="two words" accuracy=0.6122')


def test_json_lines_are_one_object_each(stream: io.StringIO) -> None:
    configure_logging("INFO", "json", stream)
    with log_context(turn="t1"):
        LOGGER.warning("rate limited; waiting", extra={"wait_s": 4.0})
    record = json.loads(stream.getvalue())
    assert record["level"] == "WARNING"
    assert record["logger"] == "pac.guardrail.application.llm_classifier"
    assert record["msg"] == "rate limited; waiting"
    assert record["turn"] == "t1"
    assert record["wait_s"] == 4.0


def test_level_filters_and_setup_is_idempotent(stream: io.StringIO) -> None:
    configure_logging("WARNING", "text", stream)
    configure_logging("WARNING", "text", stream)
    LOGGER.info("hidden")
    LOGGER.warning("shown")
    assert stream.getvalue().count("shown") == 1
    assert "hidden" not in stream.getvalue()


def test_exceptions_are_included(stream: io.StringIO) -> None:
    configure_logging("INFO", "text", stream)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        LOGGER.exception("turn failed")
    assert "RuntimeError: boom" in stream.getvalue()


async def test_context_reaches_tasks_and_is_restored() -> None:
    async def inner() -> dict[str, str]:
        await asyncio.sleep(0)
        return dict(current_context())

    with log_context(request="abc"):
        seen = await asyncio.create_task(inner())
    assert seen == {"request": "abc"}
    assert dict(current_context()) == {}
    assert len(new_id()) == 8


def test_web_server_lines_are_logged_once_without_internals(stream: io.StringIO) -> None:
    configure_logging("INFO", "text", stream)
    logging.getLogger("uvicorn.error").info("Started", extra={"color_message": "\x1b[36mStarted"})
    assert stream.getvalue().count("Started") == 1
    assert "color_message" not in stream.getvalue()
    assert " uvicorn Started" in stream.getvalue()
    assert "uvicorn.error" not in stream.getvalue()
