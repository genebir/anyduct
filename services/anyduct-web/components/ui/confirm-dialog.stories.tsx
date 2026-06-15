import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { ConfirmDialog } from "./confirm-dialog";
import { Button } from "./button";

const meta: Meta<typeof ConfirmDialog> = {
  title: "Primitives/ConfirmDialog",
  component: ConfirmDialog,
  tags: ["autodocs"],
  parameters: { layout: "fullscreen" },
};

export default meta;
type Story = StoryObj<typeof ConfirmDialog>;

export const Destructive: Story = {
  render: () => {
    const Demo = () => {
      const [open, setOpen] = useState(false);
      const [loading, setLoading] = useState(false);
      return (
        <div className="p-12">
          <Button variant="destructive" onClick={() => setOpen(true)}>
            Delete pipeline
          </Button>
          <ConfirmDialog
            open={open}
            title="Delete pipeline?"
            description="All versions and schedules will be removed. Past runs stay for audit. This cannot be undone."
            confirmLabel="Delete"
            destructive
            loading={loading}
            onCancel={() => setOpen(false)}
            onConfirm={() => {
              setLoading(true);
              setTimeout(() => {
                setLoading(false);
                setOpen(false);
              }, 800);
            }}
          />
        </div>
      );
    };
    return <Demo />;
  },
};

export const Confirm: Story = {
  render: () => {
    const Demo = () => {
      const [open, setOpen] = useState(false);
      return (
        <div className="p-12">
          <Button onClick={() => setOpen(true)}>Trigger pipeline</Button>
          <ConfirmDialog
            open={open}
            title="Trigger this pipeline now?"
            description="A new run will be enqueued for the next available worker."
            confirmLabel="Trigger"
            onCancel={() => setOpen(false)}
            onConfirm={() => setOpen(false)}
          />
        </div>
      );
    };
    return <Demo />;
  },
};
