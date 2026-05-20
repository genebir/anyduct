import type { Meta, StoryObj } from "@storybook/react-vite";
import { DataTable, type Column } from "./data-table";
import { StatusBadge } from "./status-badge";
import { EmptyState } from "./empty-state";
import { InboxIcon } from "lucide-react";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";

interface Pipeline {
  id: string;
  name: string;
  mode: "batch" | "stream";
  lastRunStatus: "running" | "succeeded" | "failed" | "cancelled" | "pending";
  schedules: number;
}

const SAMPLE_ROWS: Pipeline[] = [
  {
    id: "p1",
    name: "orders-nightly",
    mode: "batch",
    lastRunStatus: "succeeded",
    schedules: 1,
  },
  {
    id: "p2",
    name: "user-signups-stream",
    mode: "stream",
    lastRunStatus: "running",
    schedules: 1,
  },
  {
    id: "p3",
    name: "weekly-rollup",
    mode: "batch",
    lastRunStatus: "failed",
    schedules: 0,
  },
];

const COLUMNS: Column<Pipeline>[] = [
  { key: "name", header: "Name", cell: (row) => row.name },
  {
    key: "mode",
    header: "Mode",
    cell: (row) => (
      <span className="text-xs uppercase tracking-wide text-text-secondary">
        {row.mode}
      </span>
    ),
  },
  {
    key: "status",
    header: "Last run",
    cell: (row) => <StatusBadge status={row.lastRunStatus} />,
  },
  {
    key: "schedules",
    header: "Schedules",
    cell: (row) => <span className="tabular-nums">{row.schedules}</span>,
  },
];

const meta: Meta<typeof DataTable<Pipeline>> = {
  title: "Primitives/DataTable",
  component: DataTable<Pipeline>,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <Story />
      </MockLocaleProvider>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof DataTable<Pipeline>>;

export const Basic: Story = {
  args: {
    columns: COLUMNS,
    rows: SAMPLE_ROWS,
  },
};

export const Empty: Story = {
  args: {
    columns: COLUMNS,
    rows: [],
    emptyState: (
      <EmptyState
        icon={<InboxIcon className="h-8 w-8" aria-hidden />}
        title="No pipelines yet"
      />
    ),
  },
};

export const Clickable: Story = {
  args: {
    columns: COLUMNS,
    rows: SAMPLE_ROWS,
    onRowClick: (row) => {
      // eslint-disable-next-line no-console
      console.log("clicked", row.id);
    },
  },
};
