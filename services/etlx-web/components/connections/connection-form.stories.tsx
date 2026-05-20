import type { Meta, StoryObj } from "@storybook/react-vite";
import { ConnectionForm } from "./connection-form";
import type { ConnectionSummary } from "@/lib/api";
import { MockLocaleProvider } from "../../.storybook/mocks/providers";

/**
 * The form's "Create connection" button calls the real REST client, which
 * will 401 inside Storybook (no JWT, no server). That's intentional — the
 * stories are for visual + a11y verification of the multi-step
 * connector-type-aware form, not behavior. The submit-error toast also
 * needs a <Toaster /> in the tree, which Storybook intentionally omits so
 * the canvas stays clean.
 */

const FAKE_EXISTING: ConnectionSummary = {
  id: "c-1",
  workspace_id: "ws-1",
  type: "postgres",
  name: "prod-orders-db",
  config_json: {
    host: "db.acme.example",
    port: 5432,
    database: "orders",
    user: "etlx",
  },
  secret_refs: ["password"],
};

const meta: Meta<typeof ConnectionForm> = {
  title: "Connections/ConnectionForm",
  component: ConnectionForm,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <Story />
      </MockLocaleProvider>
    ),
  ],
  args: {
    workspaceId: "ws-1",
    onSaved: () => undefined,
    onCancel: () => undefined,
  },
};

export default meta;
type Story = StoryObj<typeof ConnectionForm>;

export const CreateMode: Story = {
  args: { mode: "create" },
};

export const EditMode: Story = {
  args: { mode: "edit", existing: FAKE_EXISTING },
};

export const EditHttp: Story = {
  args: {
    mode: "edit",
    existing: {
      ...FAKE_EXISTING,
      id: "c-2",
      type: "http",
      name: "acme-api",
      config_json: { base_url: "https://api.acme.example", timeout_seconds: 30 },
      secret_refs: ["auth_token"],
    },
  },
};

export const EditMongo: Story = {
  args: {
    mode: "edit",
    existing: {
      ...FAKE_EXISTING,
      id: "c-3",
      type: "mongodb",
      name: "prod-mongo",
      config_json: { uri: "mongodb://mongo:27017", database: "prod" },
      secret_refs: ["password"],
    },
  },
};
