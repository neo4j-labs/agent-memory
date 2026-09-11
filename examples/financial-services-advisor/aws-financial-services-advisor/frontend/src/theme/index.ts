import { createSystem, defaultConfig, defineConfig } from '@chakra-ui/react'

/**
 * Neo4j Labs theme for the AWS Financial Services Advisor console.
 *
 * Ported from `examples/lennys-memory/frontend/src/theme/index.ts` so the two
 * example apps look like siblings.
 *
 * - Labs Purple `#6366F1` is the primary accent (`brand`)
 * - Neo4j Teal `#009999` is the secondary accent (`teal`)
 * - Amber `#F59E0B` marks experimental / warning states
 *
 * Components should use **semantic tokens** (`bg`, `bg.panel`, `bg.subtle`,
 * `fg`, `fg.muted`, `border`, `brand.fg`, `colorPalette.subtle`, ...) rather
 * than literal palette steps, so light and dark mode both work.
 */
const config = defineConfig({
  globalCss: {
    'html, body': {
      bg: 'bg',
      color: 'fg',
    },
  },
  theme: {
    tokens: {
      colors: {
        brand: {
          50: { value: '#EEF2FF' },
          100: { value: '#E0E7FF' },
          200: { value: '#C7D2FE' },
          300: { value: '#A5B4FC' },
          400: { value: '#818CF8' },
          500: { value: '#6366F1' }, // Labs Purple
          600: { value: '#4F46E5' },
          700: { value: '#4338CA' },
          800: { value: '#3730A3' },
          900: { value: '#312E81' },
          950: { value: '#1E1B4B' },
        },
        teal: {
          50: { value: '#E6F7F7' },
          100: { value: '#CCEFEF' },
          200: { value: '#99DFDF' },
          300: { value: '#66CFCF' },
          400: { value: '#33BFBF' },
          500: { value: '#009999' }, // Neo4j Teal
          600: { value: '#007A7A' },
          700: { value: '#005C5C' },
          800: { value: '#003D3D' },
          900: { value: '#001F1F' },
          950: { value: '#001010' },
        },
        amber: {
          50: { value: '#FFFBEB' },
          100: { value: '#FEF3C7' },
          200: { value: '#FDE68A' },
          300: { value: '#FCD34D' },
          400: { value: '#FBBF24' },
          500: { value: '#F59E0B' },
          600: { value: '#D97706' },
          700: { value: '#B45309' },
          800: { value: '#92400E' },
          900: { value: '#78350F' },
          950: { value: '#451A03' },
        },
      },
      fonts: {
        heading: { value: "'Syne', system-ui, -apple-system, 'Segoe UI', sans-serif" },
        body: { value: "'Public Sans', system-ui, -apple-system, 'Segoe UI', sans-serif" },
        mono: { value: "'JetBrains Mono', 'Fira Code', Consolas, Monaco, monospace" },
      },
    },
    semanticTokens: {
      colors: {
        brand: {
          solid: { value: '{colors.brand.500}' },
          contrast: { value: 'white' },
          fg: { value: { _light: '{colors.brand.600}', _dark: '{colors.brand.400}' } },
          muted: { value: { _light: '{colors.brand.100}', _dark: '{colors.brand.900}' } },
          subtle: { value: { _light: '{colors.brand.50}', _dark: '{colors.brand.950}' } },
          emphasized: { value: { _light: '{colors.brand.200}', _dark: '{colors.brand.800}' } },
          focusRing: { value: '{colors.brand.500}' },
        },
        teal: {
          solid: { value: '{colors.teal.500}' },
          contrast: { value: 'white' },
          fg: { value: { _light: '{colors.teal.600}', _dark: '{colors.teal.400}' } },
          muted: { value: { _light: '{colors.teal.100}', _dark: '{colors.teal.900}' } },
          subtle: { value: { _light: '{colors.teal.50}', _dark: '{colors.teal.950}' } },
          emphasized: { value: { _light: '{colors.teal.200}', _dark: '{colors.teal.800}' } },
          focusRing: { value: '{colors.teal.500}' },
        },
        amber: {
          solid: { value: '{colors.amber.500}' },
          contrast: { value: 'white' },
          fg: { value: { _light: '{colors.amber.600}', _dark: '{colors.amber.400}' } },
          muted: { value: { _light: '{colors.amber.100}', _dark: '{colors.amber.900}' } },
          subtle: { value: { _light: '{colors.amber.50}', _dark: '{colors.amber.950}' } },
          emphasized: { value: { _light: '{colors.amber.200}', _dark: '{colors.amber.800}' } },
          focusRing: { value: '{colors.amber.500}' },
        },
      },
    },
  },
})

export const labsSystem = createSystem(defaultConfig, config)

/**
 * Colours for NVL graph nodes, keyed by Neo4j label.
 *
 * NVL paints on a canvas, so these have to be literal values rather than
 * Chakra tokens. They are deliberately mid-tone so they read in both themes.
 */
export const nodeColors: Record<string, string> = {
  Customer: '#68BDF6',
  Person: '#DA7194',
  Organization: '#F79767',
  Transaction: '#FFD86E',
  Alert: '#FF6B6B',
  SanctionedEntity: '#E74C3C',
  SanctionAlias: '#E6B0AA',
  PEP: '#9B59B6',
  PEPRelative: '#BB8FCE',
  Document: '#A5D6A7',
  Investigation: '#F39C12',
  Entity: '#C990C0',
}

export const defaultNodeColor = '#A5ABB6'

/** Colour for a node, matched against its labels (case-insensitively). */
export function getNodeColor(labels: string[]): string {
  for (const label of labels) {
    if (nodeColors[label]) return nodeColors[label]
    const match = Object.keys(nodeColors).find((key) => key.toLowerCase() === label.toLowerCase())
    if (match) return nodeColors[match]
  }
  return defaultNodeColor
}
