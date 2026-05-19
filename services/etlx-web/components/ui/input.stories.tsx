import type { Meta, StoryObj } from "@storybook/react-vite";
import { Input } from "./input";

const meta: Meta<typeof Input> = {
  title: "Primitives/Input",
  component: Input,
  tags: ["autodocs"],
  argTypes: {
    invalid: { control: "boolean" },
    disabled: { control: "boolean" },
  },
};

export default meta;
type Story = StoryObj<typeof Input>;

export const Default: Story = {
  args: {
    placeholder: "Pipeline name",
  },
};

export const WithValue: Story = {
  args: {
    defaultValue: "orders-nightly",
  },
};

export const Invalid: Story = {
  args: {
    defaultValue: "invalid name with spaces",
    invalid: true,
    "aria-describedby": "input-error",
  },
  render: (args) => (
    <div className="space-y-1">
      <Input {...args} />
      <p id="input-error" className="text-xs text-error">
        Names must be slug-safe (lowercase letters, digits, hyphens).
      </p>
    </div>
  ),
};

export const Disabled: Story = {
  args: {
    defaultValue: "read-only",
    disabled: true,
  },
};

export const WithLabel: Story = {
  render: (args) => (
    <label className="block space-y-1">
      <span className="text-sm font-medium text-text">Workspace slug</span>
      <Input {...args} placeholder="acme-data" />
      <span className="text-xs text-text-secondary">
        Slug used in URLs and YAML configs.
      </span>
    </label>
  ),
};
