import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { ColumnsField } from "./columns-field";
import { en, type Messages } from "@/lib/i18n/messages";

/**
 * Column multi-select (ADR-0033). Live introspection needs a backend, so these
 * stories exercise the no-source state (free-text add only) and a pre-selected
 * value showing the removable "extra" chips for columns not in the introspected
 * set. The checklist itself appears once a connection + table resolve at runtime.
 */

const t = (k: keyof Messages) => en[k];

function Harness({ initial, withCtx }: { initial?: string[]; withCtx?: boolean }) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <div style={{ width: 280 }}>
      <ColumnsField
        value={value}
        workspaceId={withCtx ? "ws-1" : undefined}
        connectionId={withCtx ? "conn-1" : undefined}
        table={withCtx ? "orders" : undefined}
        onChange={setValue}
        t={t}
      />
      <pre className="mt-2 text-[11px] text-text-muted">{JSON.stringify(value ?? null)}</pre>
    </div>
  );
}

const meta: Meta = {
  title: "Builder/ColumnsField",
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj;

export const NoSource: Story = {
  render: () => <Harness />,
};

export const WithSavedColumns: Story = {
  render: () => <Harness initial={["id", "amount", "legacy_col"]} />,
};
