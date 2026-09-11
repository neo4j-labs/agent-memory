import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * Flat ESLint config for the chat frontend.
 *
 * `next lint` was removed in Next 16, so `npm run lint` calls `eslint .`
 * directly and this file is the only source of rules. `eslint-config-next`
 * 16 ships flat configs, so no `FlatCompat` shim is needed.
 *
 * `eslint-config-next` already registers `eslint-plugin-react-hooks`, so
 * this file only raises `react-hooks/exhaustive-deps` from a warning to an
 * error: stale-closure effects are exactly the class of bug this example
 * used to ship (a graph effect that never refetched when the active thread
 * changed).
 */
const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", ".chakra/**"],
  },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // Pinned explicitly so `eslint-plugin-react` does not have to sniff the
    // installed React version (its detection path breaks under ESLint 10).
    settings: { react: { version: "19.2" } },
    rules: {
      "react-hooks/exhaustive-deps": "error",
      // Chakra v3 passes an unknown token straight through as a literal CSS
      // value, so `bg="bg.canvas"` silently compiled to `background:
      // bg.canvas` — a declaration the browser discards. These are the v2
      // token names that have no v3 equivalent, plus the v2 `isDisabled`
      // prop. (`colorScheme` is *not* listed: in v3 it is a real style prop
      // for the CSS `color-scheme` property; the v2 palette prop is
      // `colorPalette`.)
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "Literal[value=/^(bg|fg)\\.(canvas|default)$/], JSXAttribute[name.name='isDisabled']",
          message:
            "Chakra v2 API: use bg / bg.subtle / fg, or `disabled` (see the Chakra v3 migration guide).",
        },
      ],
    },
  },
];

export default config;
