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

export type FieldType = "string" | "number" | "password";

export interface ConnectorField {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  placeholder?: string;
  help?: string;
  defaultValue?: string | number;
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

export const CONNECTOR_SCHEMAS: ConnectorSchema[] = [
  POSTGRES,
  MYSQL,
  SQLITE,
  S3,
  KAFKA,
  HTTP,
];

export function findSchema(type: string): ConnectorSchema | undefined {
  return CONNECTOR_SCHEMAS.find((s) => s.type === type);
}
