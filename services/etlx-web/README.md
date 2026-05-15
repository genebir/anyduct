# etlx-web

Next.js (App Router) frontend for the etl-plugins web UI. **Step 7.1
placeholder** — currently a single `/` page that confirms the build wires up.
Design tokens, layout shell, and the Pipeline Builder land in Step 10.

This package is a **pnpm workspace member**. Run from the repo root:

```bash
pnpm --filter @etlx/web install
pnpm --filter @etlx/web dev          # http://localhost:3000
pnpm --filter @etlx/web typecheck
pnpm --filter @etlx/web build
```

Design system SSOT: [`../../DESIGN.md`](../../DESIGN.md). Token usage outside
the system (arbitrary colors/spacing) is a PR-reject (see ADR-0018 and
`CLAUDE.md` §6).
