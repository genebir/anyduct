import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusBadge } from "./status-badge";

const meta: Meta<typeof StatusBadge> = {
  title: "Primitives/StatusBadge",
  component: StatusBadge,
  tags: ["autodocs"],
  argTypes: {
    status: {
      control: "select",
      options: [
        "pending",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
      ],
    },
  },
};

export default meta;
type Story = StoryObj<typeof StatusBadge>;

export const Pending: Story = { args: { status: "pending" } };
export const Queued: Story = { args: { status: "queued" } };
export const Running: Story = { args: { status: "running" } };
export const Succeeded: Story = { args: { status: "succeeded" } };
export const Failed: Story = { args: { status: "failed" } };
export const Cancelled: Story = { args: { status: "cancelled" } };
export const Skipped: Story = { args: { status: "skipped" } };

export const AllStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <StatusBadge status="pending" />
      <StatusBadge status="queued" />
      <StatusBadge status="running" />
      <StatusBadge status="succeeded" />
      <StatusBadge status="failed" />
      <StatusBadge status="cancelled" />
      <StatusBadge status="skipped" />
    </div>
  ),
};
