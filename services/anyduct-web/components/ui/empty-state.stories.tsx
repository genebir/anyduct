import type { Meta, StoryObj } from "@storybook/react-vite";
import { InboxIcon, AlertTriangleIcon } from "lucide-react";
import { EmptyState } from "./empty-state";
import { Button } from "./button";

const meta: Meta<typeof EmptyState> = {
  title: "Primitives/EmptyState",
  component: EmptyState,
  tags: ["autodocs"],
};

export default meta;
type Story = StoryObj<typeof EmptyState>;

export const NoPipelines: Story = {
  args: {
    icon: <InboxIcon className="h-10 w-10" aria-hidden />,
    title: "No pipelines yet",
    description:
      "Pipelines move data between connections. Create one to get started.",
    action: <Button>New pipeline</Button>,
  },
};

export const FailedFetch: Story = {
  args: {
    icon: <AlertTriangleIcon className="h-10 w-10 text-error" aria-hidden />,
    title: "Couldn't load runs",
    description:
      "Server returned 503. Check that anyduct-server is reachable, then retry.",
    action: <Button variant="secondary">Retry</Button>,
  },
};

export const TitleOnly: Story = {
  args: { title: "Nothing here." },
};
