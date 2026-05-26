"""OpenLineageEmitter (ADR-0041 K5) — event shape + HTTP behaviour.

Pins the wire shape against the OpenLineage 2.0.2 spec the consumer
end (Marquez, DataHub, …) will parse against, and confirms the emit
path swallows network errors so a lineage backend outage never fails
a run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from etl_plugins.core.asset import AssetKey
from etl_plugins.observability.lineage import COMPLETE, FAIL, START, LineageEvent
from etl_plugins.observability.openlineage import (
    OpenLineageEmitter,
    build_run_event,
)


def _event(
    *,
    et: str = START,
    run_id: str | None = None,
    name: str = "p",
    inputs: tuple[AssetKey, ...] = (),
    outputs: tuple[AssetKey, ...] = (),
    records_read: int | None = None,
    records_written: int | None = None,
    error: str | None = None,
) -> LineageEvent:
    return LineageEvent(
        event_type=et,
        run_id=run_id or str(uuid4()),
        job_name=name,
        inputs=inputs,
        outputs=outputs,
        records_read=records_read,
        records_written=records_written,
        error=error,
    )


# ---------- build_run_event (pure conversion) ------------------------------


def test_build_event_has_required_openlineage_top_level_keys() -> None:
    payload = build_run_event(_event(), namespace="prod")
    for key in ("eventType", "eventTime", "producer", "schemaURL", "run", "job"):
        assert key in payload, f"missing {key}"
    assert payload["job"]["namespace"] == "prod"
    assert payload["job"]["name"] == "p"
    assert payload["inputs"] == [] and payload["outputs"] == []


def test_dataset_ref_splits_assetkey_on_slash() -> None:
    payload = build_run_event(
        _event(
            inputs=(AssetKey.of("wh", "public.users"),),
            outputs=(AssetKey.of("dst", "public.users_copy"),),
        ),
        namespace="prod",
    )
    assert payload["inputs"] == [{"namespace": "prod:wh", "name": "public.users"}]
    assert payload["outputs"] == [{"namespace": "prod:dst", "name": "public.users_copy"}]


def test_metrics_facet_emitted_when_record_counts_present() -> None:
    payload = build_run_event(
        _event(et=COMPLETE, records_read=100, records_written=98),
        namespace="prod",
    )
    metrics = payload["run"]["facets"]["metrics"]
    assert metrics["records_read"] == 100
    assert metrics["records_written"] == 98


def test_metrics_facet_omitted_when_record_counts_absent() -> None:
    payload = build_run_event(_event(), namespace="prod")
    assert "metrics" not in payload["run"]["facets"]


def test_error_facet_for_fail_event() -> None:
    payload = build_run_event(
        _event(et=FAIL, error="connector died"),
        namespace="prod",
    )
    err = payload["run"]["facets"]["errorMessage"]
    assert err["message"] == "connector died"
    assert err["programmingLanguage"] == "PYTHON"


def test_runid_passthrough_when_already_uuid() -> None:
    rid = str(uuid4())
    payload = build_run_event(_event(run_id=rid), namespace="prod")
    assert payload["run"]["runId"] == rid


def test_runid_coerced_to_uuid_when_not_uuid() -> None:
    """Synthetic ids ("local-run-1") become deterministic UUIDs so the
    consumer's UUID validator doesn't reject the event, and the same
    string always lands on the same OL run."""
    payload1 = build_run_event(_event(run_id="local-run-1"), namespace="prod")
    payload2 = build_run_event(_event(run_id="local-run-1"), namespace="prod")
    # Both successfully parse as UUIDs and are stable.
    UUID(payload1["run"]["runId"])
    assert payload1["run"]["runId"] == payload2["run"]["runId"]


# ---------- OpenLineageEmitter.emit (HTTP) ---------------------------------


class _StubTransport(httpx.BaseTransport):
    """Capture HTTP requests in-memory so we can assert the wire bytes
    without needing a real OpenLineage backend in unit tests."""

    def __init__(self, *, status: int = 201, raise_on_send: Exception | None = None) -> None:
        self._status = status
        self._raise = raise_on_send
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise is not None:
            raise self._raise
        self.requests.append(request)
        return httpx.Response(self._status, json={"ok": True})


def _emitter(transport: _StubTransport, **kw: Any) -> OpenLineageEmitter:
    client = httpx.Client(transport=transport)
    return OpenLineageEmitter("http://marquez:5000", client=client, **kw)


def test_emit_posts_to_lineage_endpoint_with_json_payload() -> None:
    t = _StubTransport()
    emitter = _emitter(t)
    emitter.emit(_event(et=START, inputs=(AssetKey.of("wh", "users"),)))
    assert len(t.requests) == 1
    req = t.requests[0]
    assert req.url.path.endswith("/api/v1/lineage")
    assert req.method == "POST"
    body = req.read().decode("utf-8")
    assert '"eventType":"START"' in body or '"eventType": "START"' in body


def test_emit_requires_endpoint_url() -> None:
    with pytest.raises(ValueError, match="endpoint_url"):
        OpenLineageEmitter("")


def test_emit_attaches_api_key_as_bearer_when_set() -> None:
    t = _StubTransport()
    emitter = _emitter(t, api_key="sk-test")  # pragma: allowlist secret
    emitter.emit(_event())
    auth = t.requests[0].headers.get("authorization", "")
    assert auth == "Bearer sk-test"  # pragma: allowlist secret


def test_emit_does_not_raise_on_network_error() -> None:
    """A backend outage must never fail the run — the emit call returns
    cleanly even when the HTTP layer raises."""
    t = _StubTransport(raise_on_send=httpx.ConnectError("connection refused"))
    emitter = _emitter(t)
    emitter.emit(_event(et=START))  # must not raise


def test_emit_does_not_raise_on_non_2xx_response() -> None:
    """Same posture for 4xx/5xx — log and move on."""
    t = _StubTransport(status=503)
    emitter = _emitter(t)
    emitter.emit(_event(et=FAIL, error="upstream gone"))  # must not raise
    assert len(t.requests) == 1


def test_endpoint_url_trailing_slash_normalized() -> None:
    """``http://host:5000/`` and ``http://host:5000`` resolve identically."""
    t = _StubTransport()
    client = httpx.Client(transport=t)
    emitter = OpenLineageEmitter("http://marquez:5000/", client=client)
    emitter.emit(_event())
    assert str(t.requests[0].url) == "http://marquez:5000/api/v1/lineage"


def test_close_is_idempotent() -> None:
    """Double-close must not raise — important for server shutdown paths
    that may run cleanup twice (signal handler + lifespan close)."""
    t = _StubTransport()
    emitter = _emitter(t)
    emitter.close()
    emitter.close()  # no error
