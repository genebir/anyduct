"""Connection CRUD + secret handling + test-connection (Step 8.5c).

Public surface:

* :class:`ConnectionRepository` — async DB access for ``connections``.
* :class:`SecretWalker` — separates the user-supplied marker pattern
  (``{"$secret": "<key>"}`` + ``secrets: {key: value}``) into a
  sanitized config + the list of backend paths used.
* :class:`ConnectionTester` — resolves placeholders against the secret
  backend, constructs the connector through the core's
  :class:`ConnectorRegistry`, and runs ``connect`` / ``health_check`` /
  ``close``.
"""

from anyduct_server.connections.repository import (
    ConnectionNameTakenError,
    ConnectionRepository,
)
from anyduct_server.connections.secrets import (
    SecretBackendReadOnlyError,
    SecretMarkerError,
    SecretWalker,
)
from anyduct_server.connections.tester import ConnectionTester, ConnectionTestOutcome

__all__ = [
    "ConnectionNameTakenError",
    "ConnectionRepository",
    "ConnectionTestOutcome",
    "ConnectionTester",
    "SecretBackendReadOnlyError",
    "SecretMarkerError",
    "SecretWalker",
]
