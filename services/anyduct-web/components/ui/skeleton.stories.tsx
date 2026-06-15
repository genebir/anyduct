import type { Meta, StoryObj } from "@storybook/react-vite";
import { Skeleton, TableSkeleton } from "./skeleton";

const meta = {
  title: "UI/Skeleton",
  component: Skeleton,
} satisfies Meta<typeof Skeleton>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Bar: Story = {
  render: () => <Skeleton className="h-4 w-48" />,
};

export const Circle: Story = {
  render: () => <Skeleton className="h-10 w-10 rounded-full" />,
};

export const Table: Story = {
  render: () => (
    <div className="w-[480px]">
      <TableSkeleton />
    </div>
  ),
};
