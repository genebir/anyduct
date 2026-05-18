"""HTTP batch source — fetch JSON records from a REST endpoint.

Single-page or paginated GET via ``httpx``. Each row in the response body
becomes one :class:`Record`.

Response shape support:

* Top-level JSON array — ``[{"id": 1, ...}, {"id": 2, ...}]``
* Top-level JSON object with a ``records_field`` key whose value is an
  array — e.g. ``{"items": [...], "next_cursor": "..."}``.

Pagination:

* ``page_param`` (default ``None``) — when set, the connector loops
  appending ``?<page_param>=N`` (starting from ``start_page``, default
  ``1``) until the page returns 0 records or ``max_pages`` is hit.
* No pagination → single request.

Auth:

* ``auth_token`` — sent as ``Authorization: Bearer <token>``.
* ``headers`` — arbitrary extra headers (e.g. ``X-Api-Key: …``).

Why ``httpx`` and not stdlib ``urllib``: needed for timeouts, retries, JSON
decoding, and consistent test fixturing via ``httpx.MockTransport`` (so
unit tests don't need a real HTTP server).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from etl_plugins.core.connector import BatchSource
from etl_plugins.core.exceptions import ConfigError, ConnectError, ReadError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("http")
class HttpConnector(BatchSource):
    """Batch HTTP source for JSON-returning REST endpoints."""

    def __init__(
        self,
        base_url: str = "",
        *,
        auth_token: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        verify_ssl: bool = True,
        transport: httpx.BaseTransport | None = None,
        **extra: Any,
    ) -> None:
        if not base_url:
            raise ConfigError("http connector requires 'base_url'")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"http connector: 'base_url' must start with http:// or https:// (got {base_url!r})"
            )
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.extra_headers: dict[str, str] = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
        # ``transport`` is used by unit tests to inject ``httpx.MockTransport``;
        # production callers leave it ``None`` so httpx uses its real transport.
        self._transport = transport
        self._client: httpx.Client | None = None

    # --- Connector ABC ----------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        headers = {"Accept": "application/json", **self.extra_headers}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                verify=self.verify_ssl,
                headers=headers,
                transport=self._transport,
            )
        except httpx.HTTPError as e:  # pragma: no cover — defensive
            raise ConnectError(f"http: failed to open client: {e}") from e

    def close(self) -> None:
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def health_check(self) -> bool:
        """Issue a no-op GET to ``base_url`` and treat 2xx/3xx/401/403 as live.

        4xx-but-authenticated responses still mean "the server is up — auth is
        the issue", which is a config concern, not a reachability concern. 5xx
        and connection errors fail the check.
        """
        self.connect()
        assert self._client is not None  # connect() enforces
        try:
            response = self._client.get("/")
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    # --- BatchSource ------------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        """Yield records from a paginated JSON endpoint.

        Parameters
        ----------
        query
            Path appended to ``base_url`` (e.g. ``/v1/orders``). If empty,
            ``base_url`` is used verbatim.
        chunk_size
            Ignored — HTTP responses are page-sized by the server. Kept for
            ABC compatibility.
        options
            * ``params``: dict of query params sent on every request.
            * ``records_field``: when the response is a JSON object, the
              field that contains the list of records (default ``"items"``).
              ``None`` forces "response must be a list".
            * ``page_param``: query-param name for page number. ``None``
              disables pagination.
            * ``start_page``: first page number (default 1).
            * ``max_pages``: cap on pages fetched, even if the server keeps
              returning records (default 1000 — sanity guard).
        """
        self.connect()
        assert self._client is not None

        path = (query or "").strip() or "/"
        params: dict[str, Any] = dict(options.get("params") or {})
        records_field: str | None = options.get("records_field", "items")
        page_param: str | None = options.get("page_param")
        start_page: int = int(options.get("start_page", 1))
        max_pages: int = int(options.get("max_pages", 1000))

        if page_param is None:
            yield from self._fetch_page(path, params, records_field)
            return

        for page in range(start_page, start_page + max_pages):
            page_params = {**params, page_param: page}
            yielded_any = False
            for record in self._fetch_page(path, page_params, records_field):
                yielded_any = True
                yield record
            if not yielded_any:
                return  # Empty page = end of pagination.

    # --- helpers ----------------------------------------------------------

    def _fetch_page(
        self,
        path: str,
        params: dict[str, Any],
        records_field: str | None,
    ) -> Iterator[Record]:
        assert self._client is not None
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise ReadError(f"http: GET {path} failed: {e}") from e
        if response.status_code >= 400:
            raise ReadError(
                f"http: GET {path} returned {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as e:
            raise ReadError(f"http: GET {path} response was not JSON: {e}") from e

        records = _extract_records(payload, records_field=records_field, path=path)
        for raw in records:
            if not isinstance(raw, dict):
                raise ReadError(
                    f"http: GET {path} yielded a non-object record: {type(raw).__name__}"
                )
            yield Record(data=raw)


def _extract_records(payload: Any, *, records_field: str | None, path: str) -> list[dict[str, Any]]:
    """Pull the list of records out of a JSON response.

    A top-level list is yielded straight through. A top-level dict is
    indexed by ``records_field`` (default ``items``). ``records_field=None``
    + non-list response is an error.
    """
    if isinstance(payload, list):
        return payload
    if records_field is None:
        raise ReadError(
            f"http: GET {path} returned a JSON object but records_field is None — "
            "set records_field to the key that contains the list of records."
        )
    if isinstance(payload, dict):
        nested = payload.get(records_field)
        if nested is None:
            raise ReadError(f"http: GET {path} response object has no {records_field!r} field")
        if not isinstance(nested, list):
            raise ReadError(
                f"http: GET {path} field {records_field!r} is "
                f"{type(nested).__name__}, expected list"
            )
        return nested
    raise ReadError(
        f"http: GET {path} response was {type(payload).__name__}, expected list or object"
    )


__all__ = ["HttpConnector"]
