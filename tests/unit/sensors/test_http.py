"""HTTP sensor (ADR-0041 K3a) — wire behaviour + soft-fail contract."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import build_sensor
from etl_plugins.sensors.http import HttpSensor


class _StubTransport(httpx.BaseTransport):
    """Captures requests + returns canned responses so we don't need a real
    HTTP server for unit tests. Optionally raises a transport-level error to
    exercise the soft-fail path."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: str = "ok",
        raise_on_send: Exception | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._raise = raise_on_send
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise is not None:
            raise self._raise
        self.requests.append(request)
        return httpx.Response(self._status, text=self._body)


def _sensor(transport: _StubTransport, **kw: Any) -> HttpSensor:
    client = httpx.Client(transport=transport)
    return HttpSensor(url="http://example.test/healthz", client=client, **kw)


# ---- happy path -------------------------------------------------------------


def test_check_triggers_on_default_expect_status_200() -> None:
    s = _sensor(_StubTransport(status=200, body="ready"))
    r = s.check()
    assert r.triggered is True
    assert r.metadata["status"] == 200


def test_check_does_not_trigger_when_status_unexpected() -> None:
    """503 with default expect=[200] is a soft-fail; the sensor reports
    triggered=False with a useful message — never raises."""
    s = _sensor(_StubTransport(status=503, body="oops"))
    r = s.check()
    assert r.triggered is False
    assert "503" in (r.message or "")
    assert r.metadata["status"] == 503


def test_check_triggers_when_body_contains_substring() -> None:
    s = _sensor(_StubTransport(status=200, body="service is READY"), contains="READY")
    r = s.check()
    assert r.triggered is True


def test_check_does_not_trigger_when_body_missing_substring() -> None:
    s = _sensor(_StubTransport(status=200, body="not yet"), contains="READY")
    r = s.check()
    assert r.triggered is False
    assert "did not contain" in (r.message or "")


def test_check_triggers_on_any_of_multiple_expected_statuses() -> None:
    s = _sensor(_StubTransport(status=201), expect_status=frozenset({200, 201, 202}))
    assert s.check().triggered is True


# ---- soft-fail contract -----------------------------------------------------


def test_check_returns_triggered_false_on_network_error() -> None:
    """Network outage / DNS failure / timeout must never raise — return
    triggered=False with an error description so the scheduler logs + retries
    on the next tick."""
    t = _StubTransport(raise_on_send=httpx.ConnectError("dns failed"))
    s = _sensor(t)
    r = s.check()
    assert r.triggered is False
    assert "ConnectError" in (r.message or "")
    assert r.metadata["error_class"] == "ConnectError"


def test_check_returns_triggered_false_on_timeout() -> None:
    """ReadTimeout is a transport error — same soft-fail contract."""
    t = _StubTransport(raise_on_send=httpx.ReadTimeout("slow"))
    r = _sensor(t).check()
    assert r.triggered is False
    assert "ReadTimeout" in (r.message or "")


# ---- request shape ----------------------------------------------------------


def test_method_and_headers_forwarded() -> None:
    t = _StubTransport()
    HttpSensor(
        url="http://example.test/x",
        method="post",
        headers={"X-Probe": "anyduct"},
        client=httpx.Client(transport=t),
    ).check()
    assert len(t.requests) == 1
    req = t.requests[0]
    assert req.method == "POST"
    assert req.headers["x-probe"] == "anyduct"


# ---- builder dispatch ------------------------------------------------------


def test_builder_registered_under_http_type() -> None:
    """``build_sensor("http", config)`` returns an HttpSensor — the service
    layer dispatches on the stored ``type`` string."""
    s = build_sensor("http", {"url": "http://example.test/x"})
    assert isinstance(s, HttpSensor)


def test_builder_missing_url_raises() -> None:
    with pytest.raises(ConfigError, match="url"):
        build_sensor("http", {})


def test_builder_coerces_expect_status_int_or_list() -> None:
    """Operators write expect_status as int OR list[int]; both shapes work."""
    a = build_sensor("http", {"url": "http://x.test/", "expect_status": 204})
    b = build_sensor("http", {"url": "http://x.test/", "expect_status": [200, 201]})
    # No type errors at construction, both buildable
    assert isinstance(a, HttpSensor)
    assert isinstance(b, HttpSensor)


def test_builder_rejects_invalid_expect_status_shape() -> None:
    with pytest.raises(ConfigError, match="expect_status"):
        build_sensor("http", {"url": "http://x.test/", "expect_status": object()})


def test_builder_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ConfigError, match="timeout_seconds"):
        build_sensor("http", {"url": "http://x.test/", "timeout_seconds": 0})


def test_close_is_idempotent() -> None:
    s = _sensor(_StubTransport())
    s.close()
    s.close()  # no error
