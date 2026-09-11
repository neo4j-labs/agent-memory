"use client";

/**
 * Chakra UI v3 colour-mode snippet (the output of
 * `npx @chakra-ui/cli snippet add color-mode`, trimmed to what this app uses).
 *
 * Chakra v3 has no colour-mode state of its own: it reads the `.dark`/`.light`
 * class that `next-themes` puts on `<html>` and flips its semantic tokens
 * (`bg.panel`, `fg.muted`, …) accordingly. That is why every surface in this
 * app is painted with a token rather than `gray.50`/`white`.
 */

import { IconButton, Span } from "@chakra-ui/react";
import { ThemeProvider, useTheme } from "next-themes";
import type { ThemeProviderProps } from "next-themes";
import * as React from "react";

export type ColorModeProviderProps = ThemeProviderProps;

export function ColorModeProvider(props: ColorModeProviderProps) {
  return (
    <ThemeProvider attribute="class" disableTransitionOnChange {...props} />
  );
}

export type ColorMode = "light" | "dark";

export interface UseColorModeReturn {
  colorMode: ColorMode;
  setColorMode: (colorMode: ColorMode) => void;
  toggleColorMode: () => void;
}

export function useColorMode(): UseColorModeReturn {
  const { resolvedTheme, setTheme, forcedTheme } = useTheme();
  const colorMode = (forcedTheme || resolvedTheme) as ColorMode;
  const toggleColorMode = () => {
    setTheme(resolvedTheme === "dark" ? "light" : "dark");
  };
  return {
    colorMode,
    setColorMode: setTheme,
    toggleColorMode,
  };
}

export function useColorModeValue<T>(light: T, dark: T): T {
  const { colorMode } = useColorMode();
  return colorMode === "dark" ? dark : light;
}

/**
 * Colour-mode toggle.
 *
 * Both glyphs are rendered and CSS picks the visible one from the class
 * `next-themes` puts on `<html>`. That keeps the server and client markup
 * byte-identical: gating the icon on client state instead (`ClientOnly`, or a
 * mounted flag) makes React 19 intermittently fail hydration in a production
 * build.
 */
export function ColorModeButton(
  props: React.ComponentProps<typeof IconButton>
) {
  const { toggleColorMode } = useColorMode();
  return (
    <IconButton
      onClick={toggleColorMode}
      variant="ghost"
      aria-label="Toggle colour mode"
      size="sm"
      {...props}
    >
      <Span aria-hidden fontSize="md" lineHeight="1">
        <Span css={{ "html.dark &": { display: "none" } }}>☀</Span>
        <Span css={{ "html:not(.dark) &": { display: "none" } }}>☾</Span>
      </Span>
    </IconButton>
  );
}
