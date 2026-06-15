import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { ColumnLineageGraph } from "./column-lineage-graph";
import type { AssetColumnLineageGraphResponse } from "@/lib/api";

/**
 * Multi-hop column-lineage DAG (2026-06-12). Lanes per hop (root
 * rightmost), assets as entity cards, row-port béziers, transitive
 * hover/pin trace, "+N more columns" collapse on upstream cards.
 */

const meta: Meta<typeof ColumnLineageGraph> = {
  title: "Assets/ColumnLineageGraph",
  component: ColumnLineageGraph,
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
type Story = StoryObj<typeof ColumnLineageGraph>;

function graph(
  partial: Pick<AssetColumnLineageGraphResponse, "assets" | "edges"> &
    Partial<AssetColumnLineageGraphResponse>,
): AssetColumnLineageGraphResponse {
  return {
    id: partial.assets[0]?.id ?? "root",
    asset_key: partial.assets[0]?.asset_key ?? "wh/root",
    opaque: false,
    max_depth: 3,
    truncated: false,
    ...partial,
  };
}

/** raw → staging → mart: the canonical two-hop chain. Hover `total` on
 *  the mart to see the full transitive trace light up across all lanes. */
export const TwoHopChain: Story = {
  args: {
    depth: 3,
    graph: graph({
      assets: [
        { id: "mart", asset_key: "wh/mart", depth: 0, columns: ["id", "total"] },
        { id: "stg", asset_key: "wh/staging", depth: 1, columns: ["amount", "id"] },
        { id: "raw", asset_key: "wh/raw", depth: 2, columns: ["amt", "id"] },
      ],
      edges: [
        { from_asset_id: "raw", from_column: "id", to_asset_id: "stg", to_column: "id" },
        { from_asset_id: "raw", from_column: "amt", to_asset_id: "stg", to_column: "amount" },
        { from_asset_id: "stg", from_column: "id", to_asset_id: "mart", to_column: "id" },
        { from_asset_id: "stg", from_column: "amount", to_asset_id: "mart", to_column: "total" },
      ],
    }),
  },
};

/** Multi-source join feeding a rollup, plus a wide upstream table whose
 *  unlinked columns collapse behind "+N more". */
export const JoinWithCollapsedColumns: Story = {
  args: {
    depth: 3,
    graph: graph({
      assets: [
        {
          id: "rollup",
          asset_key: "pg/region_rollup",
          depth: 0,
          columns: ["currency", "region", "total"],
        },
        {
          id: "orders",
          asset_key: "pg/orders",
          depth: 1,
          columns: [
            "amount",
            "created_at",
            "customer_id",
            "discount",
            "id",
            "promo_code",
            "shipping",
            "status",
            "tax",
            "updated_at",
          ],
        },
        {
          id: "customers",
          asset_key: "my/customers",
          depth: 1,
          columns: ["customer_id", "region", "segment"],
        },
      ],
      edges: [
        {
          from_asset_id: "orders",
          from_column: "amount",
          to_asset_id: "rollup",
          to_column: "total",
        },
        {
          from_asset_id: "customers",
          from_column: "region",
          to_asset_id: "rollup",
          to_column: "region",
        },
      ],
    }),
  },
};

/** Depth cap hit — the "more upstream" chip appears next to the hop control. */
export const Truncated: Story = {
  args: {
    depth: 1,
    graph: graph({
      truncated: true,
      max_depth: 1,
      assets: [
        { id: "mart", asset_key: "wh/mart", depth: 0, columns: ["id", "total"] },
        { id: "stg", asset_key: "wh/staging", depth: 1, columns: ["amount", "id"] },
      ],
      edges: [
        { from_asset_id: "stg", from_column: "id", to_asset_id: "mart", to_column: "id" },
        { from_asset_id: "stg", from_column: "amount", to_asset_id: "mart", to_column: "total" },
      ],
    }),
  },
};
