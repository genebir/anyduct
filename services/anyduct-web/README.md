# anyduct-web

Next.js (App Router) frontend for the etl-plugins web UI. This slice
(Step 10 UI 1) wires up the design tokens from
[`../../dev-docs/DESIGN.md`](../../dev-docs/DESIGN.md) — the Arc-style sidebar shell, JWT auth
backed by `anyduct-server`, and read-only list pages for connections, pipelines,
schedules, and runs. The visual Pipeline Builder (drag-drop operators on
React Flow) lands in the next slice.

This package is a **pnpm workspace member**. Run from the repo root:

```bash
pnpm --filter @anyduct/web install
pnpm --filter @anyduct/web dev          # http://localhost:3000
pnpm --filter @anyduct/web typecheck
pnpm --filter @anyduct/web build
```

Configure the API endpoint by copying `.env.example` to `.env.local` and
pointing `NEXT_PUBLIC_ANYDUCT_API_URL` at the running `anyduct-server`.

## Layout

```
app/
  layout.tsx            # ThemeProvider + AuthProvider + WorkspaceProvider + AppShell
  globals.css           # DESIGN.md §11.1 tokens via @theme (Tailwind v4)
  login/page.tsx        # /auth/login → JWT in localStorage
  workspaces/page.tsx   # list + create (auto-owner)
  w/[slug]/...          # workspace-scoped routes (connections, pipelines, schedules, runs, settings)
components/
  ui/                   # Button, Input, Card, DataTable, StatusBadge, EmptyState
  shell/                # Sidebar (Arc-style accent bar), Header, AppShell
  providers/            # Auth (localStorage JWT + 401 handler), Theme (light/dark), Workspace
lib/
  api.ts                # Typed REST client, DTOs mirror anyduct_server.auth.schemas
  cn.ts                 # clsx + tailwind-merge helper
```

Design system SSOT: [`../../dev-docs/DESIGN.md`](../../dev-docs/DESIGN.md). Tokens outside the
system (arbitrary colors/spacing, inline hex, Tailwind `bg-[#…]`) are a
PR-reject (see ADR-0018 and `CLAUDE.md` §6).
