import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { SourceQueryField } from "./source-query-field";
import { en, type Messages } from "@/lib/i18n/messages";

/**
 * DB-source read builder (ADR-0033). The SQL/visual toggle and the raw-SQL
 * textarea render without a backend; the schema → table → column pickers need
 * live introspection, so these stories cover the toggle + SQL mode + the
 * "select a connection first" empty state. The mode defaults to visual for a
 * blank/parseable query and to SQL for a complex one.
 */

const t = (k: keyof Messages, vars?: Record<string, string | number>) => {
  let s: string = en[k];
  if (vars) for (const [kk, vv] of Object.entries(vars)) s = s.replace(`{${kk}}`, String(vv));
  return s;
};

function Harness({ initial, connectionId }: { initial?: string; connectionId?: string }) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <div style={{ width: 300 }}>
      <SourceQueryField
        value={value}
        placeholder="SELECT id, name FROM users"
        workspaceId={connectionId ? "ws-1" : undefined}
        connectionId={connectionId}
        onChange={setValue}
        t={t}
      />
      <pre className="mt-2 whitespace-pre-wrap text-[11px] text-text-muted">
        {typeof value === "string" ? value : "(empty)"}
      </pre>
    </div>
  );
}

const meta: Meta = {
  title: "Builder/SourceQueryField",
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj;

export const VisualNoConnection: Story = {
  render: () => <Harness />,
};

export const RawSqlMode: Story = {
  render: () => <Harness initial="SELECT u.id, o.total FROM users u JOIN orders o ON o.user_id = u.id" />,
};

export const SimpleSelectPrefilled: Story = {
  render: () => <Harness initial="SELECT id, name FROM public.users" />,
};
