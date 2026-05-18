"""Unit tests for HttpConnector (Step 5.7).

Uses ``httpx.MockTransport`` so the connector exercises the full request /
response pipeline (auth headers, query params, JSON parsing, paging logic)
without standing up a real HTTP server.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from etl_plugins.connectors.http import HttpConnector
from etl_plugins.core.exceptions import ConfigError, ReadError


def _mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.BaseTransport:
    return httpx.MockTransport(handler)


# --- construction ----------------------------------------------------------


def test_rejects_missing_base_url() -> None:
    with pytest.raises(ConfigError, match="base_url"):
        HttpConnector(base_url="")


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ConfigError, match="http://"):
        HttpConnector(base_url="ftp://example.com")


# --- happy path ------------------------------------------------------------


def test_reads_top_level_json_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/items"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/items"))
    assert [r.data for r in records] == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
    ]


def test_reads_json_object_with_records_field_default_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": 1}], "total": 1},
        )

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/orders"))
    assert [r.data for r in records] == [{"id": 1}]


def test_reads_json_object_with_custom_records_field() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"x": 1}, {"x": 2}]})

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/x", records_field="data"))
    assert [r.data for r in records] == [{"x": 1}, {"x": 2}]


# --- auth + headers --------------------------------------------------------


def test_sends_bearer_token_in_authorization_header() -> None:
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=[])

    conn = HttpConnector(
        base_url="https://api.example.test",
        auth_token="s3cr3t",  # pragma: allowlist secret
        transport=_mock(handler),
    )
    with conn:
        list(conn.read(query="/v1/items"))
    assert seen_auth == ["Bearer s3cr3t"]


def test_sends_extra_headers() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json=[])

    conn = HttpConnector(
        base_url="https://api.example.test",
        headers={"X-Api-Key": "abc", "X-Tenant": "t1"},
        transport=_mock(handler),
    )
    with conn:
        list(conn.read(query="/v1/items"))
    assert seen_headers["x-api-key"] == "abc"
    assert seen_headers["x-tenant"] == "t1"


# --- query params ----------------------------------------------------------


def test_sends_params_on_request() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url.query.decode()))
        return httpx.Response(200, json=[])

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        list(conn.read(query="/v1/items", params={"status": "active", "limit": 50}))
    assert "status=active" in captured[0]
    assert "limit=50" in captured[0]


# --- pagination ------------------------------------------------------------


def test_paginates_until_empty_page() -> None:
    pages: dict[int, list[dict[str, Any]]] = {
        1: [{"i": 1}, {"i": 2}],
        2: [{"i": 3}],
        3: [],  # signals end of pagination
    }
    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "0"))
        seen_pages.append(page)
        return httpx.Response(200, json=pages[page])

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/items", page_param="page", start_page=1))
    assert [r.data["i"] for r in records] == [1, 2, 3]
    assert seen_pages == [1, 2, 3]


def test_pagination_respects_max_pages() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        # Server keeps returning records forever; max_pages should stop us.
        return httpx.Response(200, json=[{"id": 1}])

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/items", page_param="page", max_pages=3))
    assert len(records) == 3  # one record per page, capped


# --- errors ----------------------------------------------------------------


def test_non_2xx_status_raises_read_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn, pytest.raises(ReadError, match="403"):
        list(conn.read(query="/v1/private"))


def test_non_json_body_raises_read_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn, pytest.raises(ReadError, match="not JSON"):
        list(conn.read(query="/v1/items"))


def test_missing_records_field_raises_read_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0})  # no 'items'

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn, pytest.raises(ReadError, match="'items'"):
        list(conn.read(query="/v1/items"))


def test_non_object_record_raises_read_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])  # records must be dicts

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn, pytest.raises(ReadError, match="non-object"):
        list(conn.read(query="/v1/ids"))


def test_records_field_none_with_object_response_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": "bar"})

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn, pytest.raises(ReadError, match="records_field is None"):
        list(conn.read(query="/v1/x", records_field=None))


# --- health check ----------------------------------------------------------


def test_health_check_returns_true_on_2xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    assert conn.health_check() is True
    conn.close()


def test_health_check_returns_true_on_unauthorized() -> None:
    """401 means the server is up — auth is a config concern."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "auth required"})

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    assert conn.health_check() is True
    conn.close()


def test_health_check_returns_false_on_5xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream gone")

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    assert conn.health_check() is False
    conn.close()


def test_health_check_returns_false_on_connection_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS failed")

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    assert conn.health_check() is False
    conn.close()


# --- registry --------------------------------------------------------------


def test_registered_as_http_in_connector_registry() -> None:
    from etl_plugins.core.registry import ConnectorRegistry

    cls = ConnectorRegistry.get("http")
    assert cls is HttpConnector


# --- record data should be a plain dict, not lazy ---------------------------


def test_records_are_deep_copied_dicts() -> None:
    payload = [{"id": 1, "nested": {"x": "y"}}]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

    conn = HttpConnector(base_url="https://api.example.test", transport=_mock(handler))
    with conn:
        records = list(conn.read(query="/v1/items"))
    assert records[0].data == payload[0]
