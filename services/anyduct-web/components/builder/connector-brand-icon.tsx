/**
 * Vendor brand icons for connector operators (2026-06-12, user request
 * — "MySQL은 MySQL 아이콘"). SVG path data from `simple-icons`, wrapped
 * to match the lucide icon signature so `OperatorSpec.icon` stays a
 * drop-in `ComponentType<LucideProps>` (palette chips, canvas nodes,
 * properties panel all keep working unchanged).
 *
 * Vendors missing from simple-icons (AWS family, Vertica, SQL Server —
 * removed upstream for trademark policy) keep their lucide fallbacks in
 * operators.ts.
 */

import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";
import {
  siApachecassandra,
  siApachekafka,
  siClickhouse,
  siGooglebigquery,
  siMongodb,
  siMysql,
  siNatsdotio,
  siPostgresql,
  siRabbitmq,
  siRedis,
  siSnowflake,
  siSqlite,
  type SimpleIcon,
} from "simple-icons";

function brandIcon(icon: SimpleIcon): ComponentType<LucideProps> {
  function Brand({ size = 24, ...rest }: LucideProps) {
    return (
      <svg
        viewBox="0 0 24 24"
        width={size}
        height={size}
        fill="currentColor"
        aria-hidden="true"
        {...rest}
      >
        <path d={icon.path} />
      </svg>
    );
  }
  Brand.displayName = `Brand(${icon.title})`;
  return Brand;
}

export const PostgresIcon = brandIcon(siPostgresql);
export const MysqlIcon = brandIcon(siMysql);
export const SqliteIcon = brandIcon(siSqlite);
export const SnowflakeIcon = brandIcon(siSnowflake);
export const BigqueryIcon = brandIcon(siGooglebigquery);
export const ClickhouseIcon = brandIcon(siClickhouse);
export const MongodbIcon = brandIcon(siMongodb);
export const CassandraIcon = brandIcon(siApachecassandra);
export const RedisIcon = brandIcon(siRedis);
export const KafkaIcon = brandIcon(siApachekafka);
export const RabbitmqIcon = brandIcon(siRabbitmq);
export const NatsIcon = brandIcon(siNatsdotio);
