import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { TriggerParamsDialog } from "./trigger-params-dialog";
import { Button } from "../ui/button";
import type { PipelineSummary } from "@/lib/api";

const meta: Meta<typeof TriggerParamsDialog> = {
  title: "Pipelines/TriggerParamsDialog",
  component: TriggerParamsDialog,
  tags: ["autodocs"],
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <Story />
      </MockLocaleProvider>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof TriggerParamsDialog>;

const PIPELINE = {
  id: "p1",
  name: "orders_to_dw",
  current_config_json: {
    params: { region: "kr", limit: 100, day: "{{ ds }}" },
  },
} as unknown as PipelineSummary;

export const WithParams: Story = {
  render: () => {
    const Demo = () => {
      const [open, setOpen] = useState(true);
      return (
        <div className="p-12">
          <Button onClick={() => setOpen(true)}>Run with parameters</Button>
          <TriggerParamsDialog
            open={open}
            workspaceId="ws1"
            pipeline={PIPELINE}
            onClose={() => setOpen(false)}
          />
        </div>
      );
    };
    return <Demo />;
  },
};
