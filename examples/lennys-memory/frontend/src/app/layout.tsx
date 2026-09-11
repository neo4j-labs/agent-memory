import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import { Syne, Public_Sans, JetBrains_Mono } from "next/font/google";
import { Provider } from "@/components/ui/provider";
import "./globals.css";

// Neo4j Labs typography
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
  title: "Lenny's Memory - Neo4j Labs",
  description:
    "Explore Lenny's Podcast with AI-powered graph memory - A Neo4j Labs Demo",
  icons: {
    icon: "/favicon.svg",
  },
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
        {/* Opt-in only: a fork should not send page views to someone else's
            project. The hosted Neo4j Labs demo sets this to "true". */}
        {process.env.NEXT_PUBLIC_ENABLE_ANALYTICS === "true" && <Analytics />}
      </body>
    </html>
  );
}
