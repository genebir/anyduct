import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { LineageGraph } from "./lineage-graph";
import type { AssetLineageGraphResponse } from "@/lib/api";

/**
 * Multi-hop table-level lineage DAG (2026-06-12). Upstream lanes left of
 * the asset being viewed, downstream right; hover traces the transitive
 * path, clicking a card navigates.
 */

const meta: Meta<typeof LineageGraph> = {
  title: "Assets/LineageGraph",
  component: LineageGraph,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <div style={{ width: 1100, padding: 16 }}>
          <Story />
        </div>
      </MockLocaleProvider>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof LineageGraph>;

function graph(
  partial: Pick<AssetLineageGraphResponse, "assets" | "edges"> &
    Partial<AssetLineageGraphResponse>,
): AssetLineageGraphResponse {
  const root = partial.assets.find((a) => a.depth === 0);
  return {
    id: root?.id ?? "root",
    asset_key: root?.asset_key ?? "wh/root",
    max_depth: 3,
    truncated: false,
    ...partial,
  };
}

/** The asset sits mid-chain: one upstream source, one downstream mart. */
export const MidChain: Story = {
  args: {
    depth: 3,
    graph: graph({
      assets: [
        { id: "stg", asset_key: "wh/staging", kind: "table", depth: 0 },
        { id: "raw", asset_key: "wh/raw", kind: "table", depth: -1 },
        { id: "mart", asset_key: "wh/mart", kind: "table", depth: 1 },
      ],
      edges: [
        { from_asset_id: "raw", to_asset_id: "stg" },
        { from_asset_id: "stg", to_asset_id: "mart" },
      ],
    }),
  },
};

/** Fan-in + fan-out around the root, two hops each way. */
export const FanInFanOut: Story = {
  args: {
    depth: 3,
    graph: graph({
      assets: [
        { id: "root", asset_key: "pg/enriched_orders", kind: "table", depth: 0 },
        { id: "o", asset_key: "pg/orders", kind: "table", depth: -1 },
        { id: "c", asset_key: "my/customers", kind: "table", depth: -1 },
        { id: "raw", asset_key: "s3/raw_events", kind: "object", depth: -2 },
        { id: "m1", asset_key: "pg/region_mart", kind: "table", depth: 1 },
        { id: "m2", asset_key: "pg/finance_mart", kind: "table", depth: 1 },
        { id: "bi", asset_key: "pg/dashboard_feed", kind: "table", depth: 2 },
      ],
      edges: [
        { from_asset_id: "raw", to_asset_id: "o" },
        { from_asset_id: "o", to_asset_id: "root" },
        { from_asset_id: "c", to_asset_id: "root" },
        { from_asset_id: "root", to_asset_id: "m1" },
        { from_asset_id: "root", to_asset_id: "m2" },
        { from_asset_id: "m1", to_asset_id: "bi" },
      ],
    }),
  },
};

/** Depth cap hit — the "more upstream" chip appears. */
export const Truncated: Story = {
  args: {
    depth: 1,
    graph: graph({
      truncated: true,
      max_depth: 1,
      assets: [
        { id: "mart", asset_key: "wh/mart", kind: "table", depth: 0 },
        { id: "stg", asset_key: "wh/staging", kind: "table", depth: -1 },
      ],
      edges: [{ from_asset_id: "stg", to_asset_id: "mart" }],
    }),
  },
};
