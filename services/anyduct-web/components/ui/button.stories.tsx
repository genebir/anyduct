import type { Meta, StoryObj } from "@storybook/react-vite";
import { PlusIcon, TrashIcon } from "lucide-react";
import { Button } from "./button";

const meta: Meta<typeof Button> = {
  title: "Primitives/Button",
  component: Button,
  tags: ["autodocs"],
  argTypes: {
    variant: {
      control: "select",
      options: ["primary", "secondary", "ghost", "destructive", "outline"],
    },
    size: { control: "select", options: ["sm", "md", "lg"] },
    loading: { control: "boolean" },
    disabled: { control: "boolean" },
  },
};

export default meta;
type Story = StoryObj<typeof Button>;

export const Primary: Story = {
  args: {
    children: "Save changes",
    variant: "primary",
  },
};

export const Secondary: Story = {
  args: {
    children: "Cancel",
    variant: "secondary",
  },
};

export const Ghost: Story = {
  args: {
    children: "View details",
    variant: "ghost",
  },
};

export const Destructive: Story = {
  args: {
    children: "Delete workspace",
    variant: "destructive",
  },
};

export const Outline: Story = {
  args: {
    children: "Export YAML",
    variant: "outline",
  },
};

export const WithIcon: Story = {
  render: (args) => (
    <Button {...args}>
      <PlusIcon className="h-4 w-4" aria-hidden />
      New pipeline
    </Button>
  ),
  args: { variant: "primary" },
};

export const Loading: Story = {
  args: {
    children: "Saving…",
    loading: true,
  },
};

export const Disabled: Story = {
  args: {
    children: "Save",
    disabled: true,
  },
};

export const SizeRow: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button size="sm">Small</Button>
      <Button size="md">Medium</Button>
      <Button size="lg">Large</Button>
    </div>
  ),
};

export const DestructiveWithIcon: Story = {
  render: () => (
    <Button variant="destructive">
      <TrashIcon className="h-4 w-4" aria-hidden />
      Delete
    </Button>
  ),
};
