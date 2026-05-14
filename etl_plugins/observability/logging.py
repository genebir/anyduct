"""Structured logging via structlog. SPEC.md §9.2.

Provides:
    * ``mask_sensitive_values`` — structlog processor that redacts values whose
      key matches a sensitive keyword (password, secret, token, ...). Applied
      by default in :func:`configure_logging`.
    * ``configure_logging`` — one-call setup of structlog (JSON or dev console).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

import structlog

# Substring match against the (lowercased) key name. Conservative: prefer
# over-redaction to leakage. Callers can pass custom keywords to
# ``configure_logging``.
DEFAULT_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "password",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "private_key",
    "privatekey",
    "credential",
    "credentials",
    "authorization",
    "sasl_password",
)
MASK_VALUE = "***REDACTED***"


def _is_sensitive(key: str, keywords: tuple[str, ...]) -> bool:
    kl = key.lower()
    return any(kw in kl for kw in keywords)


def _redact(obj: Any, keywords: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        return {
            k: (
                MASK_VALUE
                if isinstance(k, str) and _is_sensitive(k, keywords)
                else _redact(v, keywords)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x, keywords) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(x, keywords) for x in obj)
    return obj


def make_secret_masker(
    keywords: Iterable[str] = DEFAULT_SENSITIVE_KEYWORDS,
) -> Callable[[Any, str, Mapping[str, Any]], Mapping[str, Any]]:
    """Return a structlog processor that redacts values of sensitive keys.

    Use as one of the ``processors`` in :func:`structlog.configure`.
    """
    kws = tuple(k.lower() for k in keywords)

    def processor(
        _logger: Any, _method_name: str, event_dict: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return cast("Mapping[str, Any]", _redact(dict(event_dict), kws))

    return processor


# Module-level default masker — exposed for direct import.
mask_sensitive_values = make_secret_masker()


def configure_logging(
    level: str = "INFO",
    *,
    json: bool = True,
    add_timestamp: bool = True,
    sensitive_keywords: Iterable[str] | None = None,
    extra_processors: list[Any] | None = None,
) -> None:
    """Configure structlog globally. Idempotent — safe to call repeatedly.

    Parameters
    ----------
    level
        Log level name ("DEBUG", "INFO", "WARNING", "ERROR").
    json
        If True (default), emit single-line JSON. False = colored dev console.
    add_timestamp
        If True, prepend ISO-8601 UTC timestamp to each event.
    sensitive_keywords
        Override the default redaction keyword list.
    extra_processors
        Inserted before the renderer.
    """
    masker = make_secret_masker(
        sensitive_keywords if sensitive_keywords is not None else DEFAULT_SENSITIVE_KEYWORDS
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        masker,
    ]
    if add_timestamp:
        processors.append(structlog.processors.TimeStamper(fmt="iso", utc=True))
    if extra_processors:
        processors.extend(extra_processors)
    processors.append(
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    level_int = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        cache_logger_on_first_use=False,
    )

    # 표준 logging도 동일 레벨로 맞춰서 structlog → stdlib bridge가 동작하도록.
    logging.basicConfig(level=level_int, format="%(message)s", stream=sys.stderr, force=True)
