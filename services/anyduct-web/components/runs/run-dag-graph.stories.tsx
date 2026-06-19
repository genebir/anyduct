import type { Meta, StoryObj } from "@storybook/react-vite";
import { RunDagGraph } from "./run-dag-graph";
import type { NodeRunEntry } from "@/lib/api";

/**
 * Live DAG progress for a ``node_level`` run (ADR-0041 H3c). Status-coloured
 * node cards over a BFS-depth layered layout; pending/running edges animate
 * to show the wave moving through the graph.
 */

const meta: Meta<typeof RunDagGraph> = {
  title: "Runs/RunDagGraph",
  component: RunDagGraph,
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
type Story = StoryObj<typeof RunDagGraph>;

function nr(
  node_id: string,
  kind: string,
  status: NodeRunEntry["status"],
  depends_on: string[],
  extra: Partial<NodeRunEntry> = {},
): NodeRunEntry {
  return {
    id: node_id,
    node_id,
    kind,
    status,
    depends_on,
    pending_deps: status === "pending" ? depends_on.length : 0,
    started_at: status === "pending" ? null : "2026-05-26T09:00:00Z",
    finished_at:
      status === "succeeded" || status === "failed" || status === "cancelled"
        ? "2026-05-26T09:00:01Z"
        : null,
    heartbeat_at: null,
    worker_id: status === "pending" ? null : "worker-A",
    attempt: status === "pending" ? 0 : 1,
    records_read: 0,
    records_written: 0,
    error_class: null,
    error_message: null,
    output_ref: null,
    ...extra,
  };
}

/** Diamond DAG mid-flight — a is done, b/c running in parallel, d still blocked. */
export const MidRun: Story = {
  args: {
    nodes: [
      nr("source", "source", "succeeded", [], { records_read: 1000 }),
      nr("clean", "transform", "running", ["source"]),
      nr("dedupe", "transform", "running", ["source"]),
      nr("join", "join", "pending", ["clean", "dedupe"]),
      nr("sink", "sink", "pending", ["join"]),
    ],
  },
};

/** Everything done — final state of a successful run. */
export const AllSucceeded: Story = {
  args: {
    nodes: [
      nr("source", "source", "succeeded", [], { records_read: 1000 }),
      nr("clean", "transform", "succeeded", ["source"]),
      nr("dedupe", "transform", "succeeded", ["source"]),
      nr("join", "join", "succeeded", ["clean", "dedupe"]),
      nr("sink", "sink", "succeeded", ["join"], { records_written: 940 }),
    ],
  },
};

/** A failure mid-DAG cancels the rest — downstream becomes cancelled, not failed. */
export const PartialFailure: Story = {
  args: {
    nodes: [
      nr("source", "source", "succeeded", [], { records_read: 1000 }),
      nr("clean", "transform", "succeeded", ["source"]),
      nr("dedupe", "transform", "failed", ["source"], {
        error_class: "TransformError",
        error_message: "join key 'id' missing",
      }),
      nr("join", "join", "cancelled", ["clean", "dedupe"]),
      nr("sink", "sink", "cancelled", ["join"]),
    ],
  },
};

/** Operator DAG (ADR-0099): a branch-deselected step shows "skipped" (not
 *  "cancelled"), and a dynamically-mapped (``expand``) step shows its per-instance
 *  fan-out breakdown — one instance failed here (region=eu). */
export const OperatorDagWithBranchAndFanout: Story = {
  args: {
    nodes: [
      nr("gate", "sql", "succeeded", [], { records_written: 5 }),
      nr("load_present", "sql", "succeeded", ["gate"], { records_written: 5 }),
      nr("log_absent", "sql", "cancelled", ["gate"], {
        result_json: { task_state: "skipped" },
      }),
      nr("fanout", "sql", "failed", ["load_present"], {
        records_written: 2,
        error_class: "WriteError",
        error_message: "region=eu: connection refused",
        result_json: {
          mapped_instances: [
            { map_values: { region: "us" }, records_read: 0, records_written: 1, success: true, error_class: null },
            { map_values: { region: "eu" }, records_read: 0, records_written: 0, success: false, error_class: "WriteError" },
            { map_values: { region: "apac" }, records_read: 0, records_written: 1, success: true, error_class: null },
          ],
        },
      }),
    ],
  },
};
