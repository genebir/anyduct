import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { ColumnLineageGraph } from "./column-lineage-graph";

/**
 * Column-level lineage view (ADR-0041 J3; ERD-style redesign 2026-06-12).
 * Upstream assets render as entity cards on the left, the current asset
 * as one card on the right; row-port béziers connect them. Hover a row
 * to trace its path (everything else dims); click pins the highlight.
 */

const meta: Meta<typeof ColumnLineageGraph> = {
  title: "Assets/ColumnLineageGraph",
  component: ColumnLineageGraph,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <div style={{ width: 900, height: 600, padding: 16 }}>
          <Story />
        </div>
      </MockLocaleProvider>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ColumnLineageGraph>;

/** Renames + add_constant: 3 downstream columns from one source. The constant
 *  column has no upstream edge. */
export const RenameAndConstant: Story = {
  args: {
    columns: [
      {
        name: "city",
        upstreams: [{ asset_id: "u1", asset_key: "wh/users", column: "c" }],
      },
      {
        name: "id",
        upstreams: [{ asset_id: "u1", asset_key: "wh/users", column: "a" }],
      },
      { name: "tenant", upstreams: [] }, // add_constant
    ],
  },
};

/** Join shape: one downstream column drawn from two source columns
 *  across two upstream assets. */
export const NToOneJoin: Story = {
  args: {
    columns: [
      {
        name: "merged",
        upstreams: [
          { asset_id: "a", asset_key: "wh/a", column: "x" },
          { asset_id: "b", asset_key: "wh/b", column: "y" },
        ],
      },
      {
        name: "id",
        upstreams: [{ asset_id: "a", asset_key: "wh/a", column: "id" }],
      },
    ],
  },
};

/** No edges — every downstream column is a constant / opaque expression
 *  (column exists, no upstream). */
export const AllOpaqueColumns: Story = {
  args: {
    columns: [
      { name: "computed_at", upstreams: [] },
      { name: "tenant", upstreams: [] },
      { name: "version", upstreams: [] },
    ],
  },
};

/** Empty fallback — no successful run has materialized columns yet. */
export const NoColumns: Story = {
  args: { columns: [] },
};

/** A realistic multi-source rollup: three upstream assets, a dozen
 *  downstream columns, several joins and one constant — the case the
 *  old canvas turned into confetti. */
export const RealisticRollup: Story = {
  args: {
    columns: [
      { name: "order_id", upstreams: [{ asset_id: "o", asset_key: "pg/orders", column: "id" }] },
      { name: "customer_id", upstreams: [
        { asset_id: "o", asset_key: "pg/orders", column: "customer_id" },
        { asset_id: "c", asset_key: "my/customers", column: "customer_id" },
      ] },
      { name: "region", upstreams: [{ asset_id: "c", asset_key: "my/customers", column: "region" }] },
      { name: "segment", upstreams: [{ asset_id: "c", asset_key: "my/customers", column: "segment" }] },
      { name: "amount", upstreams: [{ asset_id: "o", asset_key: "pg/orders", column: "amount" }] },
      { name: "discount", upstreams: [
        { asset_id: "o", asset_key: "pg/orders", column: "amount" },
        { asset_id: "p", asset_key: "pg/promotions", column: "rate" },
      ] },
      { name: "promo_code", upstreams: [{ asset_id: "p", asset_key: "pg/promotions", column: "code" }] },
      { name: "currency", upstreams: [] },
      { name: "loaded_at", upstreams: [] },
      { name: "net_revenue", upstreams: [
        { asset_id: "o", asset_key: "pg/orders", column: "amount" },
        { asset_id: "p", asset_key: "pg/promotions", column: "rate" },
      ] },
    ],
  },
};
