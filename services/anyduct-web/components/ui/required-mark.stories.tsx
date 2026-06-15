import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { RequiredMark } from "./required-mark";

const meta: Meta<typeof RequiredMark> = {
  title: "Primitives/RequiredMark",
  component: RequiredMark,
  decorators: [(Story) => <MockLocaleProvider><Story /></MockLocaleProvider>],
};

export default meta;
type Story = StoryObj<typeof meta>;

export const OnLabel: Story = {
  render: () => (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center gap-0.5 text-xs font-semibold uppercase tracking-wider text-text-secondary">
        Connection name
        <RequiredMark />
      </span>
      <input className="h-9 rounded-md border border-border-default bg-elevated px-3 text-sm text-text" aria-label="Connection name" />
    </label>
  ),
};
