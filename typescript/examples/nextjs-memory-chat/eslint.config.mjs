// Flat ESLint config. Next 16 removed `next lint`, so `npm run lint` calls
// `eslint .` directly and this file is the only lint configuration.
//
// ESLint 10 is not usable yet: eslint-config-next 16 still depends on
// eslint-plugin-react 7.37, which crashes on ESLint 10's rule context API.
import next from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "**/*.tsbuildinfo"],
  },
  ...next,
  ...nextTypescript,
  {
    rules: {
      // The two rules that catch stale closures and effects that never clean up
      // — the bugs a memory rail full of fetches invites.
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/rules-of-hooks": "error",
      // Advisory React Compiler rule from eslint-plugin-react-hooks 7. It fires
      // on legitimate mount-time fetches, so it warns rather than blocks.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default config;
