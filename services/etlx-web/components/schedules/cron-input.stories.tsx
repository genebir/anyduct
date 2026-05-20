import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { CronInput } from "./cron-input";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";

/**
 * Stateful wrapper so each story renders a fully interactive cron editor —
 * preset chips, live ``cronstrue`` description, and next-firing preview
 * all light up exactly the way they do in /w/[slug]/schedules.
 */
function CronInputDemo({
  initial,
  allowEmpty,
}: {
  initial: string;
  allowEmpty?: boolean;
}) {
  const [value, setValue] = useState(initial);
  return (
    <div style={{ width: 360 }}>
      <CronInput value={value} onChange={setValue} allowEmpty={allowEmpty} />
    </div>
  );
}

const meta: Meta<typeof CronInputDemo> = {
  title: "Schedules/CronInput",
  component: CronInputDemo,
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
type Story = StoryObj<typeof CronInputDemo>;

export const Daily3am: Story = { args: { initial: "0 3 * * *" } };

export const EveryFiveMinutes: Story = { args: { initial: "*/5 * * * *" } };

export const Weekdays9am: Story = { args: { initial: "0 9 * * 1-5" } };

export const Empty_BatchRejection: Story = {
  args: { initial: "" },
  parameters: {
    docs: {
      description: {
        story:
          "Empty cron on a batch schedule shows the muted requirement hint instead of an error.",
      },
    },
  },
};

export const Empty_StreamAllowed: Story = {
  args: { initial: "", allowEmpty: true },
  parameters: {
    docs: {
      description: {
        story:
          "Stream schedules don't need a cron — empty input shows an informational message instead.",
      },
    },
  },
};

export const Invalid: Story = {
  args: { initial: "0 0 12345 1 1" },
  parameters: {
    docs: {
      description: {
        story:
          "Invalid expression — cronstrue throws; the input flips to invalid styling and the message switches to error tone.",
      },
    },
  },
};
