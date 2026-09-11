/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Next 16 writes AGENTS.md/CLAUDE.md into this directory on `next dev`.
  // The repository already has its own agent instructions at the root, so
  // keep the generated copies out of the tree.
  agentRules: false,
};

export default nextConfig;
