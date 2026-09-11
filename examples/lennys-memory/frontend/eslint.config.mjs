// Flat ESLint config. Next 16 removed `next lint`, so the npm script calls
// `eslint .` directly and this file is the only lint configuration.
//
// Note: ESLint 10 is not usable here yet - eslint-config-next 16 still depends
// on eslint-plugin-react 7.37, which crashes on ESLint 10's rule context API.
import next from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "**/*.tsbuildinfo",
    ],
  },
  ...next,
  ...nextTypescript,
  {
    rules: {
      // These two are the rules that would have caught the stale-closure and
      // async-effect-cleanup bugs this example used to have. Keep them errors.
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/rules-of-hooks": "error",
      // Advisory React Compiler rule from eslint-plugin-react-hooks 7. It fires
      // on legitimate mount-time patterns (reading localStorage, kicking off a
      // fetch), so it stays a warning rather than blocking the build.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default config;
