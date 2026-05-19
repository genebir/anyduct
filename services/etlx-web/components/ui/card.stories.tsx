import type { Meta, StoryObj } from "@storybook/react-vite";
import { Card, CardHeader } from "./card";
import { Button } from "./button";

const meta: Meta<typeof Card> = {
  title: "Primitives/Card",
  component: Card,
  tags: ["autodocs"],
};

export default meta;
type Story = StoryObj<typeof Card>;

export const Basic: Story = {
  render: () => (
    <Card>
      <CardHeader
        title="Recent runs"
        description="Last 10 pipeline executions across this workspace."
      />
      <p className="text-sm text-text-secondary">No runs yet.</p>
    </Card>
  ),
};

export const WithAction: Story = {
  render: () => (
    <Card>
      <CardHeader
        title="Pipelines"
        description="3 active, 1 draft."
        action={<Button size="sm">New pipeline</Button>}
      />
      <p className="text-sm text-text-secondary">Body content here.</p>
    </Card>
  ),
};

export const Grid: Story = {
  render: () => (
    <div className="grid grid-cols-2 gap-4">
      <Card>
        <div className="text-sm text-text-secondary">Pipelines</div>
        <div className="mt-1 text-3xl font-semibold text-text">12</div>
      </Card>
      <Card>
        <div className="text-sm text-text-secondary">Active schedules</div>
        <div className="mt-1 text-3xl font-semibold text-text">8</div>
        <div className="mt-1 text-xs text-text-muted">2 paused</div>
      </Card>
    </div>
  ),
};
