import path from "node:path";

import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Emit `.next/standalone/` so the production Docker image can ship a
  // minimal Node runtime (~50 MB layer) instead of the full pnpm install
  // tree. See services/anyduct-web/Dockerfile for the matching multi-stage
  // copy pattern. Harmless for `next dev` and `next build` outside of
  // Docker; a wrapper `server.js` is produced alongside `.next/static`.
  output: "standalone",
  // pnpm workspace lives at the repo root; Next has to walk past
  // services/anyduct-web/ to discover it when tracing dependencies for the
  // standalone bundle. Without this, the trace warns about ambiguous
  // workspace roots and may copy too much or too little.
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
  experimental: {
    // Tree-shakes barrel imports from heavy libraries so we only pay the
    // bundle cost for what we actually use. lucide-react alone is the
    // big win (we import 50+ icons but the lib has 1000+). xyflow +
    // monaco optimisations are smaller but free.
    optimizePackageImports: [
      "lucide-react",
      "@xyflow/react",
      "@monaco-editor/react",
    ],
  },
};

export default config;
