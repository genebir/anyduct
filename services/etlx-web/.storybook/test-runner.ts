/**
 * Storybook test-runner config (Step 10.8, 2026-06-12) — the a11y AA
 * gate from ADR-0018/DESIGN.md: every story is loaded in a real browser
 * and checked with axe. A violation fails the run ("a11y AA 위반 머지
 * 금지"). Run via `pnpm a11y` (serves storybook-static and sweeps all
 * stories).
 */

import type { TestRunnerConfig } from "@storybook/test-runner";
import { checkA11y, injectAxe } from "axe-playwright";

const config: TestRunnerConfig = {
  async preVisit(page) {
    await injectAxe(page);
  },
  async postVisit(page) {
    await checkA11y(page, "#storybook-root", {
      detailedReport: true,
      detailedReportOptions: { html: true },
    });
  },
};

export default config;
