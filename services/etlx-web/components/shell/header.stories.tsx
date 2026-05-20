import type { Meta, StoryObj } from "@storybook/react-vite";
import { PlusIcon } from "lucide-react";
import { Header } from "./header";
import { Button } from "@/components/ui/button";
import {
  MockAuthProvider,
  MockThemeProvider,
  MockLocaleProvider,
} from "../../.storybook/mocks/providers";

const meta: Meta<typeof Header> = {
  title: "Shell/Header",
  component: Header,
  tags: ["autodocs"],
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <MockThemeProvider>
          <MockAuthProvider>
            <Story />
          </MockAuthProvider>
        </MockThemeProvider>
      </MockLocaleProvider>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof Header>;

export const TitleOnly: Story = {
  args: { title: "Pipelines" },
};

export const WithSubtitle: Story = {
  args: {
    title: "orders-nightly",
    subtitle: "Batch · last run succeeded · v3",
  },
};

export const WithActions: Story = {
  args: {
    title: "Connections",
    subtitle: "5 active, 1 errored",
    actions: (
      <Button size="sm">
        <PlusIcon className="h-4 w-4" aria-hidden />
        New connection
      </Button>
    ),
  },
};

export const Anonymous: Story = {
  args: { title: "Sign in" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <MockThemeProvider>
          <MockAuthProvider state={{ kind: "anonymous" }}>
            <Story />
          </MockAuthProvider>
        </MockThemeProvider>
      </MockLocaleProvider>
    ),
  ],
};

export const LightTheme: Story = {
  args: { title: "Settings", subtitle: "Workspace defaults" },
  decorators: [
    (Story) => (
      <MockLocaleProvider>
        <MockThemeProvider initial="light">
          <MockAuthProvider>
            <Story />
          </MockAuthProvider>
        </MockThemeProvider>
      </MockLocaleProvider>
    ),
  ],
};

export const Korean: Story = {
  args: { title: "파이프라인", subtitle: "배치 · 마지막 실행 성공 · v3" },
  decorators: [
    (Story) => (
      <MockLocaleProvider initial="ko">
        <MockThemeProvider>
          <MockAuthProvider>
            <Story />
          </MockAuthProvider>
        </MockThemeProvider>
      </MockLocaleProvider>
    ),
  ],
};
