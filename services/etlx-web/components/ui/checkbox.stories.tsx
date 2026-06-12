import type { Meta, StoryObj } from "@storybook/react-vite";
import { Checkbox } from "./checkbox";

const meta = {
  title: "Primitives/Checkbox",
  component: Checkbox,
} satisfies Meta<typeof Checkbox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const States: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <Checkbox aria-label="Unchecked" />
      <Checkbox aria-label="Checked" defaultChecked />
      <Checkbox aria-label="Some selected" indeterminate />
      <Checkbox aria-label="Disabled" disabled />
      <Checkbox aria-label="Disabled checked" disabled defaultChecked />
    </div>
  ),
};

export const WithLabel: Story = {
  render: () => (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-text">
      <Checkbox defaultChecked />
      Run after saving
    </label>
  ),
};
