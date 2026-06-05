import type { Meta, StoryObj } from "@storybook/react-vite";
import { SchemaErdGraph } from "./schema-erd-graph";

/**
 * Read-only ERD of a connection's schema (Phase AGW). Tables are entity
 * boxes listing columns; ``id`` is marked as a key and ``<x>_id`` columns
 * are inferred references (accent, ``→``) to the matching table. No backend
 * needed — tables/columns are passed in.
 */

const meta: Meta<typeof SchemaErdGraph> = {
  title: "Connections/SchemaErdGraph",
  component: SchemaErdGraph,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div style={{ width: 1000, height: 640, padding: 16 }}>
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof SchemaErdGraph>;

export const Ecommerce: Story = {
  args: {
    tables: [
      {
        table: "public.customers",
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "email", type: "VARCHAR(255)" },
          { name: "created_at", type: "TIMESTAMPTZ" },
        ],
      },
      {
        table: "public.orders",
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "customer_id", type: "BIGINT" },
          { name: "amount", type: "NUMERIC(10,2)" },
          { name: "status", type: "VARCHAR(32)" },
        ],
      },
      {
        table: "public.order_items",
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "order_id", type: "BIGINT" },
          { name: "product_id", type: "BIGINT" },
          { name: "qty", type: "INTEGER" },
        ],
      },
      {
        table: "public.products",
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "name", type: "VARCHAR(255)" },
          { name: "price", type: "NUMERIC(10,2)" },
        ],
      },
    ],
  },
};

export const SingleTable: Story = {
  args: {
    tables: [
      {
        table: "events",
        columns: [
          { name: "id", type: "BIGINT" },
          { name: "payload", type: "JSONB" },
        ],
      },
    ],
  },
};
