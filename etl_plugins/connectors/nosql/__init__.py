"""NoSQL document / KV connectors.

Currently houses :class:`MongoDBConnector`. Other document stores (Couchbase,
Cosmos DB) and KV stores (Redis, DynamoDB) will join this package as they
land — each is a separate optional extra to avoid pulling heavy drivers into
the default install.
"""

from etl_plugins.connectors.nosql.mongodb import MongoDBConnector

__all__ = ["MongoDBConnector"]
