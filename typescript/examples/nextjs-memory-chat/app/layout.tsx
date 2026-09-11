import type { Metadata } from "next";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Memory chat — Neo4j Agent Memory",
  description:
    "A Next.js travel assistant whose whole memory lives in the hosted Neo4j Agent Memory Service.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body style={{ margin: 0 }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
