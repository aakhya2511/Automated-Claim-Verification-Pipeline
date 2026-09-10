"""Structured logging setup.

Every log line is a JSON object with a stable field set. ``request_id`` is bound
to a context variable at the edge so it is attached automatically to every line
emitted while handling that request, including deep inside the rater adapter.

Two rules are enforced by construction rather than by convention:

* API keys are never logged — the settings object stores them as ``SecretStr``
  and nothing in the logging path stringifies settings.
* Raw claim text is only logged when ``observability.log_claim_text`` is on
  (default off), since it is user-supplied content.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_REDACTED = "***redacted***"
_SENSITIVE_KEYS = frozenset({"api_key", "authorization", "token", "secret", "password"})


def _add_request_id(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    request_id = _request_id.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def _redact_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Defence in depth: scrub anything that looks like a credential."""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", log_format: str = "json") -> None:
    """Install the structlog pipeline. Idempotent."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_id,
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route uvicorn/httpx records through the same handler so the log stream is
    # uniformly structured rather than a mix of formats.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level, force=True)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """Bind ``request_id`` for the duration of a block, then restore."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)
