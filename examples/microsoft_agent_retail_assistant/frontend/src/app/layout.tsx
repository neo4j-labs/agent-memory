import "./globals.css";

import type { Metadata } from "next";
import { Provider } from "@/components/ui/provider";

export const metadata: Metadata = {
  title: "Smart Shopping Assistant",
  description:
    "AI-powered shopping assistant with Neo4j memory - powered by Microsoft Agent Framework",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `suppressHydrationWarning` is required by next-themes: its inline script
    // stamps the colour-mode class on <html> before React hydrates.
    <html lang="en" suppressHydrationWarning>
      <body>
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
