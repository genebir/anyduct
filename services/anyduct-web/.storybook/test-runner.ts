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
    // Owner-accepted exception (2026-06-12): text on the brand pink /
    // error red stays WHITE by explicit owner choice — it measures
    // 3.3:1 (below AA 4.5:1) and the navy alternative was reviewed and
    // rejected on aesthetics. `.text-on-accent` carriers are excluded
    // from the sweep; every other element still gates on AA.
    await checkA11y(
      page,
      { include: [["#storybook-root"]], exclude: [[".text-on-accent"]] },
      {
        detailedReport: true,
        detailedReportOptions: { html: true },
      },
    );
  },
};

export default config;
