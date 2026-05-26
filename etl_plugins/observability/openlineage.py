"""OpenLineage emitter (ADR-0041 K5).

Ships :class:`etl_plugins.observability.lineage.LineageEvent` s as
`OpenLineage <https://openlineage.io/>`_ ``RunEvent`` POSTs to a
Marquez/OpenLineage-compatible HTTP endpoint, so any consumer of the
spec (Marquez, DataHub, Astro, …) can plug in to the catalog the
pipelines already produce internally.

Why an emitter, not a side-channel:
    The existing ``LineageEmitter`` ABC (ADR-0036 A2) is the single
    seam the runtime calls on every START/COMPLETE/FAIL. Wiring
    OpenLineage as one of its implementations means the runtime stays
    unchanged — installing the emitter is a process-startup decision.

Mapping (core ↔ OpenLineage):
    * ``LineageEvent.event_type`` → ``eventType`` (1:1; the strings
      already match the spec).
    * ``LineageEvent.run_id`` → ``run.runId`` (UUID; non-UUID ids get
      coerced through :func:`uuid.uuid5` against a stable namespace so
      old runs still produce valid OL events).
    * ``LineageEvent.job_name`` → ``job.name``. ``job.namespace`` is
      the emitter's configured namespace (default ``"etl-plugins"``).
    * Each :class:`AssetKey` ``"conn/target"`` → ``{namespace:
      "<configured>:conn", name: "target"}`` so dataset identifiers
      survive across pipeline runs and workspaces in the OL store.
    * ``records_read`` / ``records_written`` → ``runFacets.metrics``
      (custom facet) when present.
    * ``error`` → ``runFacets.errorMessage`` when present.

Failure mode: emission is **best-effort** — any HTTP error, connection
failure, or bad endpoint is caught + logged as a structlog warning and
swallowed. A lineage hiccup must never flip a successful run to failed,
mirroring the posture of the existing service-side persistence
(:class:`etlx_server.assets.repository.AssetRepository.persist_run_lineage`).

Column lineage (J1/J2/J3) ships as the standard OL ``columnLineage``
dataset facet (ADR-0041 K5b) when the event carries it — the
``runtime/builder.build_pipeline`` flow derives it once and attaches
to the ``Pipeline``; ``Pipeline.run`` then threads it through every
START/COMPLETE event. The facet attaches to each downstream output
dataset that actually has traced columns; opaque outputs (python
transform / SELECT * / join not yet supported by derivation) carry
no facet so the consumer can tell "untraceable" from "traced and
empty" by its absence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from etl_plugins.core.asset import AssetKey
from etl_plugins.core.column_lineage import ColumnLineage
from etl_plugins.observability.lineage import LineageEmitter, LineageEvent

logger = logging.getLogger(__name__)

# Producer URL identifies *this* code as the origin of the event in the
# Open Lineage store — required by the spec, free-form text in practice.
_PRODUCER = "https://github.com/etl-plugins/etl-plugins"
# Schema URLs the consumer can use to validate against the right spec
# version. Kept aligned with OpenLineage 2.x.
_RUN_EVENT_SCHEMA = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
_COLUMN_LINEAGE_FACET_SCHEMA = (
    "https://openlineage.io/spec/facets/1-0-2/ColumnLineageDatasetFacet.json"
)
# Stable namespace for coercing non-UUID run ids to deterministic UUIDs
# (UL5 keeps the mapping stable across emitter invocations for the same
# run id string).
_RUN_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://etl-plugins/openlineage/run")


def _coerce_uuid(run_id: str) -> str:
    """Return a valid UUID string for ``run.runId``.

    Core ``LineageEvent.run_id`` is typed ``str`` for callers' convenience
    (the service emits real UUIDs from ``runs.id``, but YAML / CLI runs
    use synthetic ids like ``"local-run-<n>"``). OpenLineage requires a
    UUID, so we pass through real UUIDs and uuid5-map everything else.
    """
    try:
        return str(UUID(run_id))
    except (ValueError, TypeError):
        return str(uuid5(_RUN_ID_NAMESPACE, run_id))


def _now_iso() -> str:
    # OL spec: RFC 3339 / ISO 8601 with millisecond precision + Z suffix.
    return (
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )


def _dataset_namespace_and_name(key: AssetKey, namespace_prefix: str) -> tuple[str, str]:
    """``AssetKey "conn/target"`` → ``("<prefix>:conn", "target")``. The
    OL ``columnLineage`` facet references ``inputFields`` by the same
    ``{namespace, name, field}`` shape, so this helper is shared
    between :func:`_dataset_ref` and the column-facet builder."""
    rendered = str(key)
    if "/" in rendered:
        conn, _, name = rendered.partition("/")
        return f"{namespace_prefix}:{conn}", name
    return namespace_prefix, rendered


def _dataset_ref(key: AssetKey, namespace_prefix: str) -> dict[str, Any]:
    """Render one input/output dataset reference.

    AssetKey ``"conn/target"`` lands as ``{namespace: "<prefix>:conn",
    name: "target"}``. Prefixing the connection inside the namespace
    keeps datasets from two different connections sharing the same
    target name (e.g. ``"public.users"``) distinct in the OL store —
    same separation we use internally via ``Asset.workspace_id``.
    """
    ns, name = _dataset_namespace_and_name(key, namespace_prefix)
    return {"namespace": ns, "name": name}


def _column_lineage_facets(
    column_lineage: ColumnLineage,
    *,
    namespace_prefix: str,
    producer: str,
) -> dict[str, dict[str, Any]]:
    """Group ``column_lineage.edges`` by downstream asset and render the OL
    ``ColumnLineageDatasetFacet`` for each. Returns ``{rendered_key:
    facet_dict}`` so callers can attach to matching outputs.

    Edges with at least one upstream produce ``inputFields``. Edges with
    no upstreams (constants / opaque expressions where the column exists
    but its source is undecidable) are *included* with an empty
    ``inputFields`` list — the consumer sees the column is part of the
    schema even though its origin is opaque.

    Opaque output assets (``column_lineage.opaque_assets``) get NO facet
    so the OL consumer can distinguish "traced" (facet present) from
    "untraceable" (facet absent).
    """
    opaque = {str(k) for k in column_lineage.opaque_assets}
    by_output: dict[str, dict[str, dict[str, Any]]] = {}
    for edge in column_lineage.edges:
        out_key = str(edge.downstream.asset)
        if out_key in opaque:
            continue
        fields = by_output.setdefault(out_key, {})
        input_fields = [
            {
                **dict(
                    zip(
                        ("namespace", "name"),
                        _dataset_namespace_and_name(up.asset, namespace_prefix),
                        strict=True,
                    )
                ),
                "field": up.column,
            }
            for up in edge.upstreams
        ]
        fields[edge.downstream.column] = {"inputFields": input_fields}
    return {
        key: {
            "_producer": producer,
            "_schemaURL": _COLUMN_LINEAGE_FACET_SCHEMA,
            "fields": fields,
        }
        for key, fields in by_output.items()
    }


def build_run_event(
    event: LineageEvent,
    *,
    namespace: str,
    producer: str = _PRODUCER,
) -> dict[str, Any]:
    """Pure conversion from ``LineageEvent`` to an OpenLineage RunEvent
    dict (no I/O). Split out so tests can pin the wire shape independently
    of HTTP plumbing."""
    run_facets: dict[str, Any] = {}
    if event.records_read is not None or event.records_written is not None:
        # Custom facet — not part of the core OL spec but commonly accepted
        # by stores. Keeps record counts findable per run.
        run_facets["metrics"] = {
            "_producer": producer,
            "_schemaURL": producer + "#metrics",
            "records_read": event.records_read,
            "records_written": event.records_written,
        }
    if event.error:
        # OL has a standard errorMessageRunFacet — use it for FAIL events
        # so consumers route errors through the expected channel.
        run_facets["errorMessage"] = {
            "_producer": producer,
            "_schemaURL": "https://openlineage.io/spec/facets/1-0-1/ErrorMessageRunFacet.json",
            "message": event.error,
            "programmingLanguage": "PYTHON",
        }
    # Column-lineage dataset facet (ADR-0041 K5b). Built once, attached to
    # each downstream output dataset that has traced columns. Opaque outputs
    # carry no facet so consumers can distinguish "traced" from
    # "untraceable" by its presence/absence.
    col_facets: dict[str, dict[str, Any]] = {}
    if event.column_lineage is not None:
        col_facets = _column_lineage_facets(
            event.column_lineage, namespace_prefix=namespace, producer=producer
        )
    outputs: list[dict[str, Any]] = []
    for k in event.outputs:
        ref = _dataset_ref(k, namespace)
        facet = col_facets.get(str(k))
        if facet is not None:
            ref["facets"] = {"columnLineage": facet}
        outputs.append(ref)
    payload: dict[str, Any] = {
        "eventType": event.event_type,
        "eventTime": _now_iso(),
        "producer": producer,
        "schemaURL": _RUN_EVENT_SCHEMA,
        "run": {"runId": _coerce_uuid(event.run_id), "facets": run_facets},
        "job": {"namespace": namespace, "name": event.job_name, "facets": {}},
        "inputs": [_dataset_ref(k, namespace) for k in event.inputs],
        "outputs": outputs,
    }
    return payload


class OpenLineageEmitter(LineageEmitter):
    """Post events to a Marquez/OpenLineage-compatible HTTP endpoint.

    Parameters
    ----------
    endpoint_url
        Base URL of the OL backend (e.g. ``"http://marquez:5000"``). The
        emitter POSTs to ``{endpoint_url}/api/v1/lineage``.
    namespace
        Job namespace used in every event — typically a deployment or
        environment id (``"prod"``, ``"warehouse-team"``). Default
        ``"etl-plugins"``.
    api_key
        Optional bearer token added as ``Authorization: Bearer <key>``.
    timeout_seconds
        HTTP timeout per request. Default ``5.0`` — small because we
        never want to slow a real pipeline waiting on a lineage backend.
    extra_headers
        Optional headers merged onto every request (e.g. proxy auth).
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        namespace: str = "etl-plugins",
        api_key: str | None = None,
        timeout_seconds: float = 5.0,
        extra_headers: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not endpoint_url:
            raise ValueError("OpenLineageEmitter: endpoint_url is required")
        self._url = endpoint_url.rstrip("/") + "/api/v1/lineage"
        self._namespace = namespace
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        self._headers = headers
        self._timeout = timeout_seconds
        # Tests inject a stub client; production lazily creates one so the
        # core stays import-light.
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def emit(self, event: LineageEvent) -> None:
        payload = build_run_event(event, namespace=self._namespace)
        try:
            resp = self._http().post(self._url, json=payload, headers=self._headers)
            if resp.status_code >= 400:
                logger.warning(
                    "openlineage emit non-2xx (%s) for run %s: %s",
                    resp.status_code,
                    event.run_id,
                    resp.text[:200],
                )
        except Exception as e:  # network / DNS / connect / timeout
            logger.warning(
                "openlineage emit failed for run %s: %s: %s",
                event.run_id,
                type(e).__name__,
                e,
            )

    def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


__all__ = ["OpenLineageEmitter", "build_run_event"]
