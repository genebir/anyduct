/**
 * Connector form schemas — what fields each connector type accepts.
 *
 * Mirrors the ``__init__`` signatures of the registered connectors in
 * ``etl_plugins.connectors`` (postgres / mysql / sqlite / s3 / kafka).
 * Adding a new connector to the core requires a matching entry here so the
 * Connections page can render a sensible form for it. The runtime is the
 * source of truth — the UI never sends a knob the connector wouldn't
 * recognize, but it also doesn't auto-introspect; explicit schemas keep
 * the form predictable.
 */

export type FieldType = "string" | "number" | "password" | "boolean";

export interface ConnectorField {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  help?: string;
  defaultValue?: string | number | boolean;
  /** Treat the value as a secret — sent via the ``secrets`` map with a
   *  ``{"$secret": "<key>"}`` marker in config (never reaches the metadata DB
   *  in plaintext). */
  isSecret?: boolean;
}

export interface ConnectorSchema {
  type: string;
  label: string;
  description: string;
  fields: ConnectorField[];
}

const POSTGRES: ConnectorSchema = {
  type: "postgres",
  label: "PostgreSQL",
  description: "psycopg3-backed batch source + sink for PostgreSQL databases.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 5432 },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
    {
      key: "sslmode",
      label: "SSL mode",
      type: "string",
      defaultValue: "prefer",
      help: "disable / allow / prefer / require / verify-ca / verify-full",
    },
  ],
};

const MYSQL: ConnectorSchema = {
  type: "mysql",
  label: "MySQL",
  description: "aiomysql-backed batch source + sink for MySQL / MariaDB.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 3306 },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
    { key: "charset", label: "Charset", type: "string", defaultValue: "utf8mb4" },
  ],
};

const SQLITE: ConnectorSchema = {
  type: "sqlite",
  label: "SQLite",
  description: "Local file-backed SQLite database. No credentials required.",
  fields: [
    {
      key: "database",
      label: "Database path",
      type: "string",
      required: true,
      placeholder: "/data/etlx.db",
      help: "Use ``:memory:`` for an ephemeral in-process database (won't persist between runs).",
    },
  ],
};

const MONGODB: ConnectorSchema = {
  type: "mongodb",
  label: "MongoDB",
  description: "pymongo-backed batch source + sink for MongoDB document stores.",
  fields: [
    {
      key: "uri",
      label: "URI",
      type: "string",
      required: true,
      placeholder: "mongodb://localhost:27017",
      help: "Standard mongodb:// or mongodb+srv:// connection string.",
    },
    { key: "database", label: "Database", type: "string", required: true },
    {
      key: "username",
      label: "Username",
      type: "string",
      help: "Optional — overrides any credentials embedded in the URI.",
    },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
    },
    {
      key: "auth_source",
      label: "Auth source",
      type: "string",
      placeholder: "admin",
      help: "Database to authenticate against, when different from the working database.",
    },
    {
      key: "timeout_ms",
      label: "Timeout (ms)",
      type: "number",
      defaultValue: 30000,
    },
  ],
};

const S3: ConnectorSchema = {
  type: "s3",
  label: "S3 / MinIO",
  description: "boto3-backed batch source + sink for S3-compatible object stores.",
  fields: [
    { key: "bucket", label: "Bucket", type: "string", required: true },
    { key: "region", label: "Region", type: "string", defaultValue: "us-east-1" },
    {
      key: "endpoint_url",
      label: "Endpoint URL",
      type: "string",
      placeholder: "https://s3.amazonaws.com or http://minio:9000",
      help: "Leave blank for AWS S3; set when using MinIO / R2 / etc.",
    },
    {
      key: "access_key",
      label: "Access key",
      type: "password",
      isSecret: true,
    },
    {
      key: "secret_key",
      label: "Secret key",
      type: "password",
      isSecret: true,
    },
    {
      key: "default_format",
      label: "Default format",
      type: "string",
      defaultValue: "jsonl",
      help: "parquet / csv / jsonl — used when a task doesn't override per write.",
    },
  ],
};

// Phase AGJ (2026-06-05, ADR-0081) — DynamoDB (NoSQL).
const DYNAMODB: ConnectorSchema = {
  type: "dynamodb",
  label: "DynamoDB",
  description: "boto3-backed batch source + sink for Amazon DynamoDB (serverless NoSQL).",
  fields: [
    { key: "region", label: "Region", type: "string", defaultValue: "us-east-1" },
    { key: "table", label: "Default table", type: "string", help: "Optional default table when a source/sink doesn't set one." },
    {
      key: "endpoint_url",
      label: "Endpoint URL",
      type: "string",
      placeholder: "leave blank for AWS; http://localhost:4566 for LocalStack",
      help: "Leave blank for AWS DynamoDB; set for LocalStack / local testing.",
    },
    { key: "aws_access_key_id", label: "Access key", type: "password", isSecret: true },
    { key: "aws_secret_access_key", label: "Secret key", type: "password", isSecret: true },
  ],
};

// Phase AGK (2026-06-05, ADR-0082) — Cassandra (CQL wide-column).
const CASSANDRA: ConnectorSchema = {
  type: "cassandra",
  label: "Cassandra",
  description: "cassandra-driver-backed batch source + sink for Apache Cassandra (CQL).",
  fields: [
    {
      key: "contact_points",
      label: "Contact points",
      type: "string",
      required: true,
      defaultValue: "localhost",
      help: "Comma-separated host list, e.g. node1,node2,node3.",
    },
    { key: "port", label: "Port", type: "number", defaultValue: 9042 },
    { key: "keyspace", label: "Keyspace", type: "string", help: "Default keyspace for unqualified table names." },
    { key: "username", label: "User", type: "string" },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
  ],
};

// Phase AGL (2026-06-05, ADR-0083) — Kinesis Data Streams.
const KINESIS: ConnectorSchema = {
  type: "kinesis",
  label: "Kinesis",
  description: "boto3-backed stream source + sink for Amazon Kinesis Data Streams.",
  fields: [
    { key: "region", label: "Region", type: "string", defaultValue: "us-east-1" },
    {
      key: "endpoint_url",
      label: "Endpoint URL",
      type: "string",
      placeholder: "leave blank for AWS; http://localhost:4566 for LocalStack",
      help: "Leave blank for AWS Kinesis; set for LocalStack / local testing.",
    },
    { key: "aws_access_key_id", label: "Access key", type: "password", isSecret: true },
    { key: "aws_secret_access_key", label: "Secret key", type: "password", isSecret: true },
  ],
};

// Phase AGT (2026-06-05, ADR-0086) — RabbitMQ.
const RABBITMQ: ConnectorSchema = {
  type: "rabbitmq",
  label: "RabbitMQ",
  description: "aio-pika-backed stream source + sink for RabbitMQ (AMQP) queues.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 5672 },
    { key: "virtual_host", label: "Virtual host", type: "string", defaultValue: "/" },
    { key: "username", label: "User", type: "string", defaultValue: "guest" },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
  ],
};

// Phase AGN (2026-06-05, ADR-0085) — Redis Streams.
const REDIS: ConnectorSchema = {
  type: "redis",
  label: "Redis",
  description: "redis-py-backed stream source + sink over Redis Streams (XADD / XREADGROUP).",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 6379 },
    { key: "db", label: "DB", type: "number", defaultValue: 0 },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
  ],
};

// Phase AGM (2026-06-05, ADR-0084) — SQS queue.
const SQS: ConnectorSchema = {
  type: "sqs",
  label: "SQS",
  description: "boto3-backed stream source + sink for Amazon SQS queues.",
  fields: [
    { key: "region", label: "Region", type: "string", defaultValue: "us-east-1" },
    {
      key: "endpoint_url",
      label: "Endpoint URL",
      type: "string",
      placeholder: "leave blank for AWS; http://localhost:4566 for LocalStack",
      help: "Leave blank for AWS SQS; set for LocalStack / local testing.",
    },
    { key: "aws_access_key_id", label: "Access key", type: "password", isSecret: true },
    { key: "aws_secret_access_key", label: "Secret key", type: "password", isSecret: true },
  ],
};

const HTTP: ConnectorSchema = {
  type: "http",
  label: "HTTP / REST",
  description: "httpx-backed batch source for JSON-returning REST endpoints.",
  fields: [
    {
      key: "base_url",
      label: "Base URL",
      type: "string",
      required: true,
      placeholder: "https://api.example.com",
      help: "Scheme + host (path is supplied per task via the operator's Path field).",
    },
    {
      key: "auth_token",
      label: "Bearer token",
      type: "password",
      isSecret: true,
      help: "Sent as Authorization: Bearer <token>. Leave blank for unauthenticated APIs.",
    },
    {
      key: "timeout_seconds",
      label: "Timeout (seconds)",
      type: "number",
      defaultValue: 30,
    },
  ],
};

const KAFKA: ConnectorSchema = {
  type: "kafka",
  label: "Kafka",
  description: "aiokafka-backed stream source + sink for Apache Kafka clusters.",
  fields: [
    {
      key: "bootstrap_servers",
      label: "Bootstrap servers",
      type: "string",
      required: true,
      placeholder: "kafka-1:9092,kafka-2:9092",
      help: "Comma-separated list of host:port pairs.",
    },
    {
      key: "client_id",
      label: "Client ID",
      type: "string",
      defaultValue: "etl-plugins",
    },
    {
      key: "security_protocol",
      label: "Security protocol",
      type: "string",
      defaultValue: "PLAINTEXT",
      help: "PLAINTEXT / SSL / SASL_PLAINTEXT / SASL_SSL",
    },
    {
      key: "sasl_mechanism",
      label: "SASL mechanism",
      type: "string",
      placeholder: "SCRAM-SHA-256",
    },
    {
      key: "sasl_username",
      label: "SASL username",
      type: "string",
    },
    {
      key: "sasl_password",
      label: "SASL password",
      type: "password",
      isSecret: true,
    },
  ],
};

// Phase AAQ (2026-05-29) — Vertica analytical column-store.
const VERTICA: ConnectorSchema = {
  type: "vertica",
  label: "Vertica",
  description:
    "vertica-python-backed batch source + sink for the Vertica analytical column-store.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 5433 },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
    {
      key: "ssl",
      label: "SSL / TLS",
      type: "boolean",
      defaultValue: false,
      help: "Wrap the wire connection in TLS. Requires the Vertica server to have a certificate configured.",
    },
  ],
};

// Phase AAQ (2026-05-29) — SQL Server / Azure SQL.
const MSSQL: ConnectorSchema = {
  type: "mssql",
  label: "SQL Server",
  description:
    "pymssql-backed batch source + sink for Microsoft SQL Server and Azure SQL.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 1433 },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
    {
      key: "tds_version",
      label: "TDS version",
      type: "string",
      defaultValue: "7.4",
      help: "FreeTDS protocol — 7.4 covers SQL Server 2012+ and Azure SQL.",
    },
  ],
};

// Phase AGE (2026-06-05, ADR-0077) — Snowflake cloud DW.
const SNOWFLAKE: ConnectorSchema = {
  type: "snowflake",
  label: "Snowflake",
  description:
    "snowflake-connector-python-backed batch source + sink for the Snowflake cloud data warehouse.",
  fields: [
    {
      key: "account",
      label: "Account",
      type: "string",
      required: true,
      help: "Snowflake account identifier, e.g. xy12345.eu-west-1.",
    },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
    { key: "warehouse", label: "Warehouse", type: "string", help: "Virtual warehouse to run queries on." },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "schema", label: "Schema", type: "string", defaultValue: "PUBLIC" },
    { key: "role", label: "Role", type: "string", help: "Optional session role." },
  ],
};

// Phase AGF (2026-06-05, ADR-0078) — BigQuery serverless DW.
const BIGQUERY: ConnectorSchema = {
  type: "bigquery",
  label: "BigQuery",
  description:
    "google-cloud-bigquery-backed batch source + sink for Google BigQuery (GoogleSQL).",
  fields: [
    { key: "project", label: "Project", type: "string", required: true, help: "GCP project id." },
    { key: "dataset", label: "Dataset", type: "string", required: true, help: "Default dataset for unqualified table names." },
    {
      key: "credentials_json",
      label: "Service account JSON",
      type: "password",
      isSecret: true,
      help: "Service-account key (JSON). Stored in the secret backend. Leave blank to use Application Default Credentials.",
    },
    { key: "location", label: "Location", type: "string", defaultValue: "US", help: "Dataset region, e.g. US, EU, asia-northeast3." },
  ],
};

// Phase AGG (2026-06-05, ADR-0079) — Amazon Redshift.
const REDSHIFT: ConnectorSchema = {
  type: "redshift",
  label: "Redshift",
  description:
    "redshift_connector-backed batch source + sink for Amazon Redshift (postgres-derived DW).",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, help: "Cluster endpoint, e.g. my-cluster.abc123.us-east-1.redshift.amazonaws.com." },
    { key: "port", label: "Port", type: "number", defaultValue: 5439 },
    { key: "database", label: "Database", type: "string", required: true },
    { key: "user", label: "User", type: "string", required: true },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
  ],
};

// Phase AGH (2026-06-05, ADR-0080) — ClickHouse OLAP.
const CLICKHOUSE: ConnectorSchema = {
  type: "clickhouse",
  label: "ClickHouse",
  description:
    "clickhouse-connect-backed batch source + sink for the ClickHouse column-oriented OLAP database.",
  fields: [
    { key: "host", label: "Host", type: "string", required: true, defaultValue: "localhost" },
    { key: "port", label: "Port", type: "number", defaultValue: 8123, help: "HTTP interface port (8123); 8443 for HTTPS." },
    { key: "database", label: "Database", type: "string", defaultValue: "default" },
    { key: "user", label: "User", type: "string", defaultValue: "default" },
    {
      key: "password",
      label: "Password",
      type: "password",
      isSecret: true,
      help: "Stored in the secret backend; never written to the metadata DB.",
    },
  ],
};

export const CONNECTOR_SCHEMAS: ConnectorSchema[] = [
  POSTGRES,
  MYSQL,
  SQLITE,
  VERTICA,
  MSSQL,
  SNOWFLAKE,
  BIGQUERY,
  REDSHIFT,
  CLICKHOUSE,
  MONGODB,
  DYNAMODB,
  CASSANDRA,
  REDIS,
  RABBITMQ,
  S3,
  KAFKA,
  KINESIS,
  SQS,
  HTTP,
];

export function findSchema(type: string): ConnectorSchema | undefined {
  return CONNECTOR_SCHEMAS.find((s) => s.type === type);
}
