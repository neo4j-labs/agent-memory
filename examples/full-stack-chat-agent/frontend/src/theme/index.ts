"use client";

import { createSystem, defaultConfig, defineConfig } from "@chakra-ui/react";

/**
 * Neo4j Labs theme for the news chat agent.
 *
 * Brand colours follow the Labs identity used across this repo's examples:
 *
 * - Labs Purple `#6366F1` — primary accent (`colorPalette="brand"`)
 * - Neo4j Teal  `#009999` — links and secondary accents
 * - Amber       `#F59E0B` — the "Beta" lifecycle badge
 *
 * Everything else comes from Chakra's `defaultConfig`, so the standard
 * semantic tokens (`bg`, `bg.panel`, `fg`, `fg.muted`, `border.subtle`, …)
 * are available and already colour-mode aware. Prefer those over literal
 * colours so the dark palette works without a second set of values.
 */
const config = defineConfig({
  // NOTE: `strictTokens: true` is available here and rejects any non-token
  // value, but it also rejects ordinary CSS (`"1px"`, `"100%"`, `"pointer"`)
  // and the literal hex colours the NVL palette swatches need. This example
  // instead relies on `npm run typegen` for token autocomplete plus the
  // `no-restricted-syntax` rule in `eslint.config.mjs`, which rejects the
  // Chakra v2 tokens that do not exist in v3 (`bg.canvas`, `fg.default`).
  globalCss: {
    "html, body": {
      height: "100%",
      overflow: "hidden",
    },
  },
  theme: {
    tokens: {
      colors: {
        brand: {
          50: { value: "#EEF2FF" },
          100: { value: "#E0E7FF" },
          200: { value: "#C7D2FE" },
          300: { value: "#A5B4FC" },
          400: { value: "#818CF8" },
          500: { value: "#6366F1" }, // Labs Purple
          600: { value: "#4F46E5" },
          700: { value: "#4338CA" },
          800: { value: "#3730A3" },
          900: { value: "#312E81" },
          950: { value: "#1E1B4B" },
        },
        labsTeal: {
          50: { value: "#E6F7F7" },
          100: { value: "#CCEFEF" },
          200: { value: "#99DFDF" },
          300: { value: "#66CFCF" },
          400: { value: "#33BFBF" },
          500: { value: "#009999" }, // Neo4j Teal
          600: { value: "#007A7A" },
          700: { value: "#005C5C" },
          800: { value: "#003D3D" },
          900: { value: "#001F1F" },
          950: { value: "#001010" },
        },
      },
      fonts: {
        heading: {
          value: "var(--font-syne), system-ui, -apple-system, sans-serif",
        },
        body: {
          value:
            "var(--font-public-sans), system-ui, -apple-system, sans-serif",
        },
        mono: {
          value:
            "var(--font-jetbrains-mono), ui-monospace, SFMono-Regular, Menlo, monospace",
        },
      },
    },
    semanticTokens: {
      colors: {
        brand: {
          solid: { value: "{colors.brand.500}" },
          contrast: { value: "white" },
          fg: {
            value: {
              _light: "{colors.brand.600}",
              _dark: "{colors.brand.300}",
            },
          },
          muted: {
            value: {
              _light: "{colors.brand.100}",
              _dark: "{colors.brand.900}",
            },
          },
          subtle: {
            value: { _light: "{colors.brand.50}", _dark: "{colors.brand.950}" },
          },
          emphasized: {
            value: {
              _light: "{colors.brand.200}",
              _dark: "{colors.brand.800}",
            },
          },
          focusRing: { value: "{colors.brand.500}" },
        },
        labsTeal: {
          solid: { value: "{colors.labsTeal.500}" },
          contrast: { value: "white" },
          fg: {
            value: {
              _light: "{colors.labsTeal.600}",
              _dark: "{colors.labsTeal.300}",
            },
          },
          muted: {
            value: {
              _light: "{colors.labsTeal.100}",
              _dark: "{colors.labsTeal.900}",
            },
          },
          subtle: {
            value: {
              _light: "{colors.labsTeal.50}",
              _dark: "{colors.labsTeal.950}",
            },
          },
          emphasized: {
            value: {
              _light: "{colors.labsTeal.200}",
              _dark: "{colors.labsTeal.800}",
            },
          },
          focusRing: { value: "{colors.labsTeal.500}" },
        },
      },
    },
  },
});

export const neo4jLabsSystem = createSystem(defaultConfig, config);

// `chakra typegen` (the `typegen` / `prepare` npm scripts) needs a default
// export. It regenerates Chakra's token union types from this system, which is
// what gives `bg`, `color`, `colorPalette` and friends autocomplete over the
// brand tokens defined above.
export default neo4jLabsSystem;
