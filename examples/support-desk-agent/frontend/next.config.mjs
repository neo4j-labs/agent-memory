/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next 16 writes AGENTS.md/CLAUDE.md into the project root on `next dev`.
  // This repo keeps its agent instructions at the repository root, so an
  // extra pair of files inside one example's frontend is just noise.
  agentRules: false,
  experimental: {
    // Chakra UI is a large barrel export; this keeps dev compiles and the
    // client bundle from pulling in every component. Still an experimental
    // option in Next 16 (verified against `next@16.3`), and still honoured
    // under Turbopack, which is the default bundler from Next 16 on.
    optimizePackageImports: ["@chakra-ui/react"],
  },
};

export default nextConfig;
