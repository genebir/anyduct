import type { Meta, StoryObj } from "@storybook/react-vite";
import { LineageGraph } from "./lineage-graph";

/**
 * Read-only asset lineage view (ADR-0036, Phase C). Upstream assets (left) feed
 * the current asset (centre, accent ring), which feeds downstream assets
 * (right). Reuses @xyflow/react. No backend needed — data is passed in.
 */

const meta: Meta<typeof LineageGraph> = {
  title: "Assets/LineageGraph",
  component: LineageGraph,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div style={{ width: 900, height: 460, padding: 16 }}>
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof LineageGraph>;

export const Middle: Story = {
  args: {
    current: { id: "c", asset_key: "wh/staging.events" },
    upstream: [{ id: "u1", asset_key: "lake/raw.events", kind: "object" }],
    downstream: [
      { id: "d1", asset_key: "wh/mart.daily", kind: "table" },
      { id: "d2", asset_key: "wh/mart.hourly", kind: "table" },
    ],
  },
};

export const SourceAsset: Story = {
  args: {
    current: { id: "c", asset_key: "lake/raw.events" },
    upstream: [],
    downstream: [{ id: "d1", asset_key: "wh/staging.events", kind: "table" }],
  },
};

export const LeafAsset: Story = {
  args: {
    current: { id: "c", asset_key: "wh/mart.daily" },
    upstream: [
      { id: "u1", asset_key: "wh/staging.events", kind: "table" },
      { id: "u2", asset_key: "wh/dim.customers", kind: "table" },
    ],
    downstream: [],
  },
};
