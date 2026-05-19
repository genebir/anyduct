# Connectors API

Each connector is registered under a string key — that key is what
appears in `connections.yaml` under `type:` and in
`ConnectorRegistry.get(key)`.

## RDBMS

### `postgres`

::: etl_plugins.connectors.rdbms.postgres.PostgresConnector

### `mysql`

::: etl_plugins.connectors.rdbms.mysql.MySQLConnector

### `sqlite`

::: etl_plugins.connectors.rdbms.sqlite.SQLiteConnector

## NoSQL

### `mongodb`

::: etl_plugins.connectors.nosql.mongodb.MongoDBConnector

## Object storage

### `s3`

::: etl_plugins.connectors.object_storage.s3.S3Connector

## Stream

### `kafka`

::: etl_plugins.connectors.stream.kafka.KafkaConnector

## HTTP

### `http`

::: etl_plugins.connectors.http.connector.HttpConnector
