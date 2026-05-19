import type { TestRunnerConfig } from "@storybook/test-runner";
import { getStoryContext } from "@storybook/test-runner";
import { injectAxe, checkA11y, configureAxe } from "axe-playwright";

/**
 * Storybook test-runner config — runs every story through axe-playwright
 * for an automated WCAG AA gate (CLAUDE.md hard rule: "a11y AA 위반 머지
 * 금지"). The CI step is:
 *
 *     pnpm --filter @etlx/web build-storybook
 *     npx http-server services/etlx-web/storybook-static -p 6006 &
 *     pnpm --filter @etlx/web test-storybook --url http://127.0.0.1:6006
 *
 * Individual stories can opt out by setting
 * ``parameters.a11y.disable = true`` in their meta — currently no story
 * does, but the escape hatch matches Storybook's a11y addon semantics.
 */
const config: TestRunnerConfig = {
  async preVisit(page) {
    await injectAxe(page);
  },
  async postVisit(page, context) {
    const storyContext = await getStoryContext(page, context);

    if (storyContext.parameters?.a11y?.disable) {
      return;
    }

    await configureAxe(page, {
      rules: storyContext.parameters?.a11y?.config?.rules ?? [],
    });
    await checkA11y(page, "#storybook-root", {
      detailedReport: true,
      detailedReportOptions: { html: true },
      axeOptions: storyContext.parameters?.a11y?.options ?? {},
    });
  },
};

export default config;
