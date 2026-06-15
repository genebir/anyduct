# anyduct-web production image — Next.js 15 standalone output.
#
# Three-stage build:
#
#   deps    : node:22-alpine + pnpm fetch (cache layer; lockfile-only churn)
#   builder : node:22-alpine + full repo + `pnpm --filter @anyduct/web build`
#             → produces `.next/standalone/` (minimal server.js + traced deps)
#   runtime : node:22-alpine, copies just standalone + .next/static + public.
#             Runs as a non-root user. Image ~150 MB; no pnpm, no node_modules
#             outside what `next` traced.
#
# Build context = repo root (the pnpm workspace lives there). Manual build:
#
#     docker build -f services/anyduct-web/Dockerfile -t anyduct-web:dev .
#
# `NEXT_PUBLIC_*` env vars must be set at BUILD time (Next inlines them into
# the client bundle). The runtime API URL is therefore baked in — change it
# by rebuilding with `--build-arg NEXT_PUBLIC_API_URL=...`.

# ---------- deps -----------------------------------------------------------
FROM node:22-alpine AS deps

# pnpm via corepack — the lockfile is v9 which the bundled corepack handles.
RUN corepack enable && corepack prepare pnpm@latest-9 --activate

WORKDIR /repo

# Copy workspace manifests first so this layer caches when only source
# changes. The pnpm workspace config + lockfile + each member's
# package.json are enough to resolve.
COPY pnpm-workspace.yaml pnpm-lock.yaml /repo/
COPY services/anyduct-web/package.json /repo/services/anyduct-web/

RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ---------- builder --------------------------------------------------------
FROM node:22-alpine AS builder

RUN corepack enable && corepack prepare pnpm@latest-9 --activate

WORKDIR /repo

ARG NEXT_PUBLIC_API_URL="http://localhost:8000"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1

# Pull the resolved node_modules from the deps stage, then add source.
COPY --from=deps /repo/node_modules /repo/node_modules
COPY --from=deps /repo/services/anyduct-web/node_modules /repo/services/anyduct-web/node_modules
COPY pnpm-workspace.yaml pnpm-lock.yaml /repo/
COPY services/anyduct-web /repo/services/anyduct-web

RUN pnpm --filter @anyduct/web build

# ---------- runtime --------------------------------------------------------
FROM node:22-alpine AS runtime

# `tini` for proper signal handling — Next's standalone server.js doesn't
# install its own SIGTERM handler, so we rely on tini → node to forward.
RUN apk add --no-cache tini curl \
    && addgroup -S nodejs && adduser -S nextjs -G nodejs -u 10001

WORKDIR /app

# Next's standalone bundle has its own tiny `server.js`. The `.next/static`
# and `public` dirs aren't included in the standalone tree — copy them in
# explicitly so the runtime can serve them. With outputFileTracingRoot set
# in next.config.ts, the standalone tree lives under
# `services/anyduct-web/.next/standalone/services/anyduct-web/`.
COPY --from=builder --chown=nextjs:nodejs \
    /repo/services/anyduct-web/.next/standalone /app
COPY --from=builder --chown=nextjs:nodejs \
    /repo/services/anyduct-web/.next/static /app/services/anyduct-web/.next/static
COPY --from=builder --chown=nextjs:nodejs \
    /repo/services/anyduct-web/public /app/services/anyduct-web/public

USER nextjs

ENV PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=15s \
    CMD curl -fsS http://127.0.0.1:3000/ || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "services/anyduct-web/server.js"]
