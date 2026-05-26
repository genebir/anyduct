import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
import { ColumnLineageGraph } from "./column-lineage-graph";

/**
 * Read-only column-level lineage view (ADR-0041 J3). Left column = upstream
 * columns grouped by their source asset, right column = the current asset's
 * columns (alphabetical). Edges connect each downstream column to its
 * upstream column(s). Reuses @xyflow/react.
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
