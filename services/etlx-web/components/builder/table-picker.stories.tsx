import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { TableBrowser, TableField } from "./table-picker";
import { en, type Messages } from "@/lib/i18n/messages";

/**
 * Table-picker primitives (ADR-0033). Stories render the static states — no
 * connection selected — since live introspection only fires on focus/open and
 * needs a backend. The point is the visual baseline of the input + hint and
 * the browse disclosure button.
 */

const t = (k: keyof Messages) => en[k];

function FieldHarness({ connectionId }: { connectionId?: string }) {
  const [value, setValue] = useState<unknown>("");
  return (
    <div style={{ width: 280 }}>
      <TableField
        value={value}
        placeholder="schema.table"
        workspaceId={connectionId ? "ws-1" : undefined}
        connectionId={connectionId}
        onChange={setValue}
        t={t}
      />
    </div>
  );
}

function BrowserHarness({ connectionId }: { connectionId?: string }) {
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <div style={{ width: 280 }}>
      <TableBrowser
        workspaceId={connectionId ? "ws-1" : undefined}
        connectionId={connectionId}
        onPick={setPicked}
        t={t}
      />
      {picked ? (
        <p className="mt-2 font-mono text-xs text-text-muted">SELECT * FROM {picked}</p>
      ) : null}
    </div>
  );
}

const meta: Meta = {
  title: "Builder/TablePicker",
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj;

export const FieldNoConnection: Story = {
  render: () => <FieldHarness />,
};

export const FieldWithConnection: Story = {
  render: () => <FieldHarness connectionId="conn-1" />,
};

export const BrowserButton: Story = {
  render: () => <BrowserHarness connectionId="conn-1" />,
};

export const BrowserDisabled: Story = {
  render: () => <BrowserHarness />,
};
