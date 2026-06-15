"""Node-level execution queue (ADR-0041, Phase H).

A pipeline run expands into ``node_runs`` (one per graph node); workers claim
ready nodes (all upstreams succeeded) with ``FOR UPDATE SKIP LOCKED`` so
independent branches run in parallel.
"""

from anyduct_server.node_runs.repository import NodeRunRepository, NodeSpec

__all__ = ["NodeRunRepository", "NodeSpec"]
