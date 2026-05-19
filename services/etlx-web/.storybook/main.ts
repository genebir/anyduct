import type { StorybookConfig } from "@storybook/nextjs-vite";
import tailwindcss from "@tailwindcss/vite";

/**
 * Storybook config for etlx-web (DESIGN.md §11.1 token validation +
 * ADR-0018 visual regression baseline).
 *
 * Framework: ``@storybook/nextjs-vite`` (Storybook 9). Vite handles
 * Tailwind v4 via ``@tailwindcss/vite`` so the same ``@theme`` block
 * that drives ``app/globals.css`` is available inside every story canvas.
 * The webpack-backed legacy ``@storybook/nextjs`` was incompatible with
 * Next 15.5's bundled webpack version.
 *
 * The a11y addon runs axe-core inside each story so any AA violation
 * flags during local dev. CI hooks (``pnpm --filter @etlx/web
 * build-storybook``) ensure the bundle still compiles.
 */
const config: StorybookConfig = {
  stories: ["../components/**/*.stories.@(ts|tsx)"],
  addons: [
    "@storybook/addon-docs",
    "@storybook/addon-a11y",
    "@storybook/addon-themes",
  ],
  framework: {
    name: "@storybook/nextjs-vite",
    options: {},
  },
  staticDirs: ["../public"],
  viteFinal: async (config) => {
    // Tailwind v4: register the official Vite plugin so the @theme block
    // in app/globals.css resolves the way it does in `next build`.
    config.plugins = [...(config.plugins ?? []), tailwindcss()];
    return config;
  },
};

export default config;
