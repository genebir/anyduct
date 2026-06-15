"use client";

/**
 * /w/[slug]/connections/[id]/erd — Phase AGW (2026-06-05).
 *
 * Read-only ERD of a connection's schema. Introspects the connection's
 * tables + columns (existing ADR-0033 endpoints) and renders an
 * entity-relationship graph. Relationships are inferred from ``<x>_id``
 * column names (see lib/erd.ts). Web-only — no server/core change.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeftIcon, NetworkIcon, RefreshCwIcon } from "lucide-react";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { SchemaErdGraph } from "@/components/connections/schema-erd-graph";
import { connectionsApi, type ConnectionSummary } from "@/lib/api";
import type { RawTable } from "@/lib/erd";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";

const MAX_TABLES = 60;

type Status = "loading" | "ok" | "empty" | "error";

export default function ConnectionErdPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [conn, setConn] = useState<ConnectionSummary | null>(null);
  const [tables, setTables] = useState<RawTable[] | null>(null);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<Status>("loading");

  const load = useCallback(
    async (wsId: string) => {
      setStatus("loading");
      try {
        const [list, tablesResp] = await Promise.all([
          connectionsApi.list(wsId),
          connectionsApi.tables(wsId, id),
        ]);
        setConn(list.find((c) => c.id === id) ?? null);
        const names = tablesResp.tables;
        setTotal(names.length);
        if (names.length === 0) {
          setTables([]);
          setStatus("empty");
          return;
        }
        const capped = names.slice(0, MAX_TABLES);
        // Columns are an N+1 fetch — parallelise, and soft-fail per table
        // so one unreadable table doesn't blank the whole diagram.
        const raw = await Promise.all(
          capped.map(async (table): Promise<RawTable> => {
            try {
              const r = await connectionsApi.columns(wsId, id, table);
              return { table, columns: r.columns };
            } catch {
              return { table, columns: [] };
            }
          }),
        );
        setTables(raw);
        setStatus("ok");
      } catch {
        setStatus("error");
      }
    },
    [id],
  );

  useEffect(() => {
    if (ws?.id) void load(ws.id);
  }, [ws?.id, load]);

  const truncated = total > MAX_TABLES;

  return (
    <div>
      <Header
        title={t("erd.title")}
        subtitle={conn ? conn.name : ws ? t("common.workspaceSubtitle", { name: ws.name }) : ""}
        actions={
          <div className="flex items-center gap-2">
            <Link href={`/w/${slug}/connections`}>
              <Button variant="ghost" size="sm">
                <ArrowLeftIcon size={14} />
                {t("erd.back")}
              </Button>
            </Link>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => ws?.id && void load(ws.id)}
              disabled={status === "loading"}
            >
              <RefreshCwIcon size={14} />
              {t("erd.refresh")}
            </Button>
          </div>
        }
      />

      <div className="space-y-3 p-4">
        {status === "loading" ? (
          <Card className="p-8 text-center text-sm text-text-muted">{t("erd.loading")}</Card>
        ) : status === "error" ? (
          <EmptyState
            icon={<NetworkIcon size={28} />}
            title={t("erd.error")}
          />
        ) : status === "empty" ? (
          <EmptyState
            icon={<NetworkIcon size={28} />}
            title={t("erd.empty")}
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
              <span>{t("erd.tableCount", { n: tables?.length ?? 0 })}</span>
              {truncated ? (
                <span className="text-warning">
                  {t("erd.truncated", { n: MAX_TABLES, total })}
                </span>
              ) : null}
              <span>· {t("erd.inferredNote")}</span>
            </div>
            {tables ? <SchemaErdGraph tables={tables} /> : null}
          </>
        )}
      </div>
    </div>
  );
}
