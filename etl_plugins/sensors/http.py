"""HTTP sensor — poll a URL, trigger on status + optional body match.

Example config (the service layer will hold this in the ``sensors`` table)::

    type: http
    url: https://api.example.com/healthz
    method: GET                  # optional, default GET
    expect_status: 200           # optional, default 200; int or list[int]
    contains: "ready"            # optional substring that must appear in body
    headers:                     # optional request headers
      Authorization: Bearer abc
    timeout_seconds: 5.0         # optional, default 5
    verify_tls: true             # optional, default true

The sensor returns ``triggered=True`` when the response status matches AND
(if set) ``contains`` is present in the response body. Any other outcome
(non-matching status, missing substring, network error, timeout) returns
``triggered=False`` with a descriptive ``message`` — never raises on a soft
failure, mirroring the Airflow ``HttpSensor`` contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import SensorBase, SensorResult, register_sensor


def _coerce_expect_status(raw: Any) -> frozenset[int]:
    """Accept ``200`` / ``[200, 201]`` / ``"200"`` interchangeably."""
    if raw is None:
        return frozenset({200})
    if isinstance(raw, int):
        return frozenset({raw})
    if isinstance(raw, str):
        return frozenset({int(raw)})
    if isinstance(raw, list | tuple | set | frozenset):
        out: set[int] = set()
        for v in raw:
            try:
                out.add(int(v))
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"http sensor: expect_status entry {v!r} is not an int") from exc
        if not out:
            raise ConfigError("http sensor: expect_status must contain at least one status")
        return frozenset(out)
    raise ConfigError(
        f"http sensor: expect_status must be int|list[int]|str, got {type(raw).__name__}"
    )


class HttpSensor(SensorBase):
    """Polls ``url`` and triggers when the response satisfies the configured
    status + body conditions. See module docstring for the config shape."""

    def __init__(
        self,
        *,
        url: str,
        method: str = "GET",
        expect_status: frozenset[int] = frozenset({200}),
        contains: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 5.0,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        if not url:
            raise ConfigError("http sensor: 'url' is required")
        if not isinstance(timeout_seconds, int | float) or timeout_seconds <= 0:
            raise ConfigError("http sensor: 'timeout_seconds' must be a positive number")
        self._url = url
        self._method = method.upper()
        self._expect_status = expect_status
        self._contains = contains
        self._headers = dict(headers) if headers else {}
        self._timeout = timeout_seconds
        self._verify_tls = verify_tls
        # Tests inject a stub client; production constructs one lazily so the
        # build path stays import-light.
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, verify=self._verify_tls)
        return self._client

    def check(self) -> SensorResult:
        try:
            resp = self._http().request(self._method, self._url, headers=self._headers)
        except Exception as e:  # network / DNS / connect / timeout — soft fail
            return SensorResult(
                triggered=False,
                message=f"http error: {type(e).__name__}: {e}",
                metadata={"url": self._url, "error_class": type(e).__name__},
            )
        if resp.status_code not in self._expect_status:
            return SensorResult(
                triggered=False,
                message=(
                    f"status {resp.status_code} not in expected {sorted(self._expect_status)}"
                ),
                metadata={"url": self._url, "status": resp.status_code},
            )
        body = resp.text
        if self._contains is not None and self._contains not in body:
            return SensorResult(
                triggered=False,
                message=(
                    f"status {resp.status_code} OK but body did not contain {self._contains!r}"
                ),
                metadata={"url": self._url, "status": resp.status_code},
            )
        return SensorResult(
            triggered=True,
            message=f"matched (status={resp.status_code})",
            metadata={"url": self._url, "status": resp.status_code},
        )

    def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


@register_sensor("http")
def _build_http_sensor(config: Mapping[str, Any]) -> SensorBase:
    """Builder dispatched by :func:`etl_plugins.core.sensor.build_sensor`."""
    return HttpSensor(
        url=config["url"] if "url" in config else _missing("url"),
        method=str(config.get("method", "GET")),
        expect_status=_coerce_expect_status(config.get("expect_status")),
        contains=config.get("contains"),
        headers=config.get("headers"),
        timeout_seconds=float(config.get("timeout_seconds", 5.0)),
        verify_tls=bool(config.get("verify_tls", True)),
    )


def _missing(key: str) -> Any:
    """Helper that raises a uniform ConfigError when a required key is absent.

    Keeps the builder readable — ``config[key] if key in config else _missing(key)``
    reads as "required" without an inline if-else block on every field.
    """
    raise ConfigError(f"http sensor: required config key {key!r} is missing")


__all__ = ["HttpSensor"]
