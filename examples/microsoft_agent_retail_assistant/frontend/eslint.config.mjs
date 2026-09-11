// Flat ESLint config. Next 16 removed `next lint`, so linting runs through the
// ESLint CLI (`npm run lint`) against the configs eslint-config-next exports.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/** @type {import("eslint").Linter.Config[]} */
const config = [
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
  },
  ...coreWebVitals,
  ...nextTypescript,
  {
    rules: {
      // Unused imports were how this example drifted before it had a lint
      // step; make them an error rather than something a reviewer notices.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
