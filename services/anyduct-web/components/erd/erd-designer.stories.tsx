import type { Meta, StoryObj } from "@storybook/react-vite";
import { MockLocaleProvider, MockWorkspaceProvider } from "../../.storybook/mocks/providers";
import { ErdDesigner } from "./erd-designer";

/**
 * Interactive ERD designer (Phase AGX). "Add table" creates an entity;
 * select it to edit name / columns / primary keys in the side panel; drag
 * from one table to another to add a foreign-key relationship; "Export
 * SQL" renders CREATE TABLE DDL. State auto-saves to localStorage under
 * the given workspace slug.
 */

const meta: Meta<typeof ErdDesigner> = {
  title: "ERD/ErdDesigner",
  component: ErdDesigner,
  parameters: { layout: "fullscreen", nextjs: { appDirectory: true } },
  decorators: [
    (Story) => (
      <div style={{ width: "100%", height: 640 }}>
        <MockLocaleProvider>
          <MockWorkspaceProvider>
            <Story />
          </MockWorkspaceProvider>
        </MockLocaleProvider>
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ErdDesigner>;

export const Empty: Story = {
  args: { slug: "storybook", docId: "storybook-demo" },
};
