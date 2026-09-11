import { createSystem, defaultConfig, defineConfig } from "@chakra-ui/react";

/**
 * Neo4j Labs theme for the Financial Services Advisor demo.
 *
 * Mirrors `examples/lennys-memory/frontend/src/theme/index.ts` so the Labs
 * demos look like one family:
 * - Labs Purple `#6366F1` — primary accent
 * - Neo4j Teal `#009999` — secondary / links
 *
 * Fonts are plain stacks (no bundled webfonts) to keep the container image and
 * first paint small.
 */
const config = defineConfig({
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
        teal: {
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
          value:
            "'Syne', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        },
        body: {
          value:
            "'Public Sans', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        },
        mono: {
          value: "'JetBrains Mono', 'Fira Code', Consolas, Monaco, monospace",
        },
      },
    },
    semanticTokens: {
      colors: {
        brand: {
          solid: { value: "{colors.brand.500}" },
          contrast: { value: "white" },
          fg: {
            value: { _light: "{colors.brand.600}", _dark: "{colors.brand.400}" },
          },
          muted: {
            value: { _light: "{colors.brand.100}", _dark: "{colors.brand.900}" },
          },
          subtle: {
            value: { _light: "{colors.brand.50}", _dark: "{colors.brand.950}" },
          },
          emphasized: {
            value: { _light: "{colors.brand.200}", _dark: "{colors.brand.800}" },
          },
          focusRing: { value: "{colors.brand.500}" },
        },
        teal: {
          solid: { value: "{colors.teal.500}" },
          contrast: { value: "white" },
          fg: {
            value: { _light: "{colors.teal.600}", _dark: "{colors.teal.400}" },
          },
          muted: {
            value: { _light: "{colors.teal.100}", _dark: "{colors.teal.900}" },
          },
          subtle: {
            value: { _light: "{colors.teal.50}", _dark: "{colors.teal.950}" },
          },
          emphasized: {
            value: { _light: "{colors.teal.200}", _dark: "{colors.teal.800}" },
          },
          focusRing: { value: "{colors.teal.500}" },
        },
      },
    },
  },
  globalCss: {
    body: {
      fontFamily: "body",
    },
  },
});

export const neo4jLabsSystem = createSystem(defaultConfig, config);
