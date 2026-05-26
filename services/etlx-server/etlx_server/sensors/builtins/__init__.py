"""Service-side sensor built-ins.

Import-side-effect package: importing this module registers every
service-aware sensor (sensors that need DB/server context) into the core
:mod:`etl_plugins.core.sensor` registry, alongside pure-core builtins
(``http``). The scheduler CLI + sensors REST router both import this
package at startup so :func:`etl_plugins.core.sensor.build_sensor` can
dispatch every type uniformly.

Adding a new service-side sensor:
    1. Drop a module here. Use ``@register_sensor("name")`` on the
       builder. Subclass :class:`etl_plugins.core.sensor.SensorBase` and
       override :meth:`check_async` if you need a DB session (read it
       from :data:`etlx_server.sensors.context.sensor_session_factory`).
    2. Import the module from this ``__init__`` so the side-effect runs.
    3. Add its type label to ``services/etlx-web/lib/sensors.ts``
       SENSOR_TYPES so it shows up in the create-sensor form.
"""

from __future__ import annotations

# Side-effect imports: each module's @register_sensor decorator runs at
# import time, populating etl_plugins.core.sensor._REGISTRY.
from etlx_server.sensors.builtins import asset_freshness  # noqa: F401

__all__: list[str] = []
