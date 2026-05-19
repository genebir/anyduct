import type { Preview } from "@storybook/react";
import { withThemeByDataAttribute } from "@storybook/addon-themes";
import "../app/globals.css";

/**
 * Story-wide preview. We:
 *   1. Import the same globals.css the app does so DESIGN.md §11.1 tokens
 *      (semantic colors, dark/light branches via `data-theme`) resolve in
 *      every canvas iframe.
 *   2. Provide a theme switcher that toggles `data-theme` on <html> — same
 *      mechanism `ThemeProvider` uses in production, so what you see in
 *      Storybook is exactly what ships.
 *   3. Default the a11y panel to "error" severity so AA violations are loud,
 *      and pin the surface backdrop to the navy bg-base token so contrast
 *      checks reflect real-app conditions.
 */
const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { expanded: true },
    backgrounds: {
      default: "bg-base",
      values: [
        { name: "bg-base", value: "rgb(10 18 40)" },
        { name: "bg-surface", value: "rgb(15 23 48)" },
        { name: "light", value: "rgb(248 249 253)" },
      ],
    },
    a11y: {
      // Treat every violation as a test failure — there's no "warning" tier
      // for design system gates per CLAUDE.md hard rules.
      element: "#storybook-root",
      config: {},
      options: {},
      manual: false,
    },
  },
  decorators: [
    withThemeByDataAttribute({
      themes: { dark: "dark", light: "light" },
      defaultTheme: "dark",
      attributeName: "data-theme",
    }),
  ],
};

export default preview;
