"""HTTP source connector (Step 5.7).

Reads records from REST/JSON HTTP endpoints. Batch-mode only — long-poll /
SSE consumption is a Stream concern and out of scope for this slice.

The connector is registered as ``http`` so configs can reference it:

.. code-block:: yaml

    connections:
      acme-api:
        type: http
        base_url: https://api.acme.example.com
        auth_token: ${SECRET:etlx/.../auth_token}
        timeout_seconds: 10.0
"""

from etl_plugins.connectors.http.connector import HttpConnector

__all__ = ["HttpConnector"]
