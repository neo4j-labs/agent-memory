"use client";

/**
 * Chakra UI v3 needs a provider at the root of the client tree. `defaultSystem`
 * is used as-is — this example is about the memory graph, not about theming, so
 * there is no custom token set to read past.
 */

import { ChakraProvider, defaultSystem } from "@chakra-ui/react";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return <ChakraProvider value={defaultSystem}>{children}</ChakraProvider>;
}
