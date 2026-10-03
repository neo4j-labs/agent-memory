import type { Metadata } from "next";
import { JetBrains_Mono, Public_Sans, Syne } from "next/font/google";
import { Provider } from "@/components/ui/provider";
import "./globals.css";

// Neo4j Labs typography: Syne for headings, Public Sans for body,
// JetBrains Mono for code. The CSS variables are consumed by
// `src/theme/index.ts`.
const syne = Syne({
  subsets: ["latin"],
  variable: "--font-syne",
  display: "swap",
});

const publicSans = Public_Sans({
  subsets: ["latin"],
  variable: "--font-public-sans",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Support Desk Agent — Neo4j Labs",
  description:
    "A support-desk chat agent on a stored, versioned ontology, with reasoning memory — built on neo4j-agent-memory",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${syne.variable} ${publicSans.variable} ${jetbrainsMono.variable}`}
      >
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
