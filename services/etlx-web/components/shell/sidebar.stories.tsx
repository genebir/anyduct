import type { Meta, StoryObj } from "@storybook/react-vite";
import { Sidebar } from "./sidebar";
import {
  MockWorkspaceProvider,
  DEFAULT_WORKSPACES,
} from "../../.storybook/mocks/providers";

const meta: Meta<typeof Sidebar> = {
  title: "Shell/Sidebar",
  component: Sidebar,
  tags: ["autodocs"],
  parameters: {
    layout: "fullscreen",
    nextjs: {
      appDirectory: true,
      navigation: { pathname: "/w/acme-data/pipelines" },
    },
  },
  decorators: [
    (Story) => (
      <div className="flex h-screen">
        <MockWorkspaceProvider>
          <Story />
        </MockWorkspaceProvider>
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof Sidebar>;

export const PipelinesActive: Story = {};

export const OverviewActive: Story = {
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: "/w/acme-data" } },
  },
};

export const SettingsActive: Story = {
  parameters: {
    nextjs: {
      appDirectory: true,
      navigation: { pathname: "/w/acme-data/settings" },
    },
  },
};

export const BlueWorkspace: Story = {
  decorators: [
    (Story) => (
      <div className="flex h-screen">
        <MockWorkspaceProvider currentIndex={1}>
          <Story />
        </MockWorkspaceProvider>
      </div>
    ),
  ],
  parameters: {
    nextjs: {
      appDirectory: true,
      navigation: { pathname: "/w/growth/runs" },
    },
  },
};

export const NoWorkspaces: Story = {
  decorators: [
    (Story) => (
      <div className="flex h-screen">
        <MockWorkspaceProvider workspaces={[]}>
          <Story />
        </MockWorkspaceProvider>
      </div>
    ),
  ],
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: "/workspaces" } },
  },
};

export const SoloWorkspace: Story = {
  decorators: [
    (Story) => (
      <div className="flex h-screen">
        <MockWorkspaceProvider workspaces={[DEFAULT_WORKSPACES[0]]}>
          <Story />
        </MockWorkspaceProvider>
      </div>
    ),
  ],
  parameters: {
    nextjs: {
      appDirectory: true,
      navigation: { pathname: "/w/acme-data/connections" },
    },
  },
};
