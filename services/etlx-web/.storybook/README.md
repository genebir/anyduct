# etlx-web Storybook (Step 10.8)

Visual regression + a11y harness for the design system. Tokens come from
the same `app/globals.css` the app uses (`@theme` block, DESIGN.md §11.1),
and Vite picks up Tailwind v4 via `@tailwindcss/vite` registered in
`main.ts` → `viteFinal`.

## Local

```bash
pnpm --filter @etlx/web storybook        # dev on :6006
pnpm --filter @etlx/web build-storybook  # static build to storybook-static/
```

## CI a11y gate (axe-playwright)

The test-runner (`test-runner.ts`) injects axe into every story and fails
on any WCAG AA violation — backing the CLAUDE.md hard rule
*"a11y AA 위반 머지 금지"*.

```bash
pnpm --filter @etlx/web build-storybook
pnpm dlx http-server services/etlx-web/storybook-static -p 6006 --silent &
pnpm --filter @etlx/web test-storybook --url http://127.0.0.1:6006
```

The first run on a fresh CI worker also needs Playwright browsers:

```bash
pnpm exec playwright install --with-deps chromium
```

## Story conventions

* Story files live next to the component (`button.stories.tsx` beside
  `button.tsx`) and are excluded from `next build` because the App Router
  only picks up route files in `app/`.
* Type imports come from `@storybook/react-vite` (Storybook 9 renamed
  away from `@storybook/react` for the vite framework).
* Use `tags: ["autodocs"]` on each meta so the Docs tab auto-renders.
* Higher-level components that depend on React context (Auth / Theme /
  Workspace providers) currently aren't storied — they need a small
  fake-provider wrapper, tracked under Step 10.8 follow-ups.
