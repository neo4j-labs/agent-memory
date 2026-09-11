/** @type {import('next').NextConfig} */
const nextConfig = {
  // Chakra UI re-exports several hundred symbols from one entry point; this
  // keeps the dev-server module graph (and cold compile) small. Still flagged
  // experimental in Next 16, but stable in practice for barrel-file packages.
  experimental: {
    optimizePackageImports: ["@chakra-ui/react"],
  },
};

export default nextConfig;
