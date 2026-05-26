"""Built-in sensor implementations (ADR-0041 K3).

Importing the package triggers the registration side-effects for each
built-in via the ``@register_sensor`` decorator — same pattern as
:mod:`etl_plugins.runtime.transforms`. External packages may add their
own sensor types by importing :func:`etl_plugins.core.sensor.register_sensor`
and decorating their builder.

Currently shipped:
    * ``http`` (:mod:`etl_plugins.sensors.http`) — polls a URL, triggers on
      status-code + optional response-body substring match.
"""

from __future__ import annotations

from etl_plugins.sensors import http as _http  # noqa: F401 — register side-effect

__all__: list[str] = []
