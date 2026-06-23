import type { Meta, StoryObj } from "@storybook/react-vite";
import { ReactFlow, type Node, type Edge } from "@xyflow/react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";
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
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <Story />
      </MockLocaleProvider>
    ),
  ],
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

export const IncompleteSource: Story = {
  args: {
    nodes: [nodeFromOperator("source:postgres", {})],
  },
  parameters: {
    docs: {
      description: {
        story:
          "A source/sink with no connection shows an amber warning state so the unfinished node is visible on the canvas.",
      },
    },
  },
};

// Orchestration operator steps (ADR-0099) — kind "operator". These render with
// BOTH handles (a step depends-on and is-depended-on) and a "Step" category
// label; the summary leads with the step name (the depends_on identifier).
export const LoadOperator: Story = {
  args: {
    nodes: [
      nodeFromOperator("op:load", {
        name: "load_mart",
        connection: "warehouse",
        table: "mart.daily_sales",
        mode: "overwrite",
      }),
    ],
  },
};

export const SqlOperator: Story = {
  args: {
    nodes: [
      nodeFromOperator("op:sql", {
        name: "write_start_log",
        connection: "warehouse",
        statement: "INSERT INTO ops.batch_log (step) VALUES ('START')",
      }),
    ],
  },
};

export const ProcCallOperator: Story = {
  args: {
    nodes: [
      nodeFromOperator("op:proc_call", {
        name: "run_rollup",
        connection: "warehouse",
        procedure: "ops.daily_rollup",
        args: [],
      }),
    ],
  },
};
