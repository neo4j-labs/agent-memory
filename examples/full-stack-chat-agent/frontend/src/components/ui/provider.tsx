"use client";

import { ChakraProvider } from "@chakra-ui/react";
import { neo4jLabsSystem } from "@/theme";
import { ColorModeProvider } from "./color-mode";

export function Provider({ children }: { children: React.ReactNode }) {
  return (
    <ChakraProvider value={neo4jLabsSystem}>
      <ColorModeProvider>{children}</ColorModeProvider>
    </ChakraProvider>
  );
}
