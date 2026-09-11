import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Next 16 writes AGENTS.md / CLAUDE.md into the app directory on `next dev`.
  // This repository already has its own agent instructions at the root, so keep
  // the generated copies out of the tree.
  agentRules: false,
};

export default nextConfig;
