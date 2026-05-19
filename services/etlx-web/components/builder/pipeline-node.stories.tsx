import type { Meta, StoryObj } from "@storybook/react-vite";
import { ReactFlow, type Node, type Edge } from "@xyflow/react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import "@xyflow/react/dist/style.css";

/**
 * Stories render the node inside a small React Flow canvas so the source/
 * target handles and ring-on-select states match what shows up in the
 * builder. The handlers are wired to no-ops so canvas interaction doesn't
 * crash; the on-canvas appearance is the point.
 */

const NODE_TYPES = { pipeline: PipelineNode };

function StoryCanvas({ nodes, edges = [] }: { nodes: Node[]; edges?: Edge[] }) {
  return (
    <div style={{ width: 320, height: 220 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        nodesDraggable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        fitView
        proOptions={{ hideAttribution: true }}
      />
    </div>
  );
}

function nodeFromOperator(
  operatorId: string,
  values: Record<string, unknown>,
  options: { selected?: boolean; canRemove?: boolean } = {},
): Node {
  return {
    id: "n1",
    type: "pipeline",
    position: { x: 30, y: 60 },
    data: {
      operatorId,
      values,
      selected: options.selected,
      canRemove: options.canRemove ?? true,
      onSelect: () => undefined,
      onRemove: () => undefined,
    } satisfies PipelineNodeData,
  };
}

const meta: Meta<typeof StoryCanvas> = {
  title: "Builder/PipelineNode",
  component: StoryCanvas,
  tags: ["autodocs"],
  parameters: { layout: "centered" },
};

export default meta;
type Story = StoryObj<typeof StoryCanvas>;

export const PostgresSource: Story = {
  args: {
    nodes: [
      nodeFromOperator("source:postgres", {
        connection: "prod-db",
        query: "SELECT id, name, created_at FROM users WHERE active",
      }),
    ],
  },
};

export const FilterTransform: Story = {
  args: {
    nodes: [
      nodeFromOperator("transform:filter", {
        expr: "data['amount'] > 0",
      }),
    ],
  },
};

export const S3Sink: Story = {
  args: {
    nodes: [
      nodeFromOperator("sink:s3", {
        connection: "data-lake",
        key: "exports/2026/orders.parquet",
        format: "parquet",
      }),
    ],
  },
};

export const MongoSource: Story = {
  args: {
    nodes: [
      nodeFromOperator("source:mongodb", {
        connection: "prod-mongo",
        query: "users",
        filter: '{"active": true}',
      }),
    ],
  },
};

export const Selected: Story = {
  args: {
    nodes: [
      nodeFromOperator(
        "transform:cast",
        { columns: '{"amount": "float"}' },
        { selected: true },
      ),
    ],
  },
};

export const NotConfigured: Story = {
  args: {
    nodes: [nodeFromOperator("transform:python", {})],
  },
};
