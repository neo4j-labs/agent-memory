import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// Flat config (ESLint 10). `--ext` no longer exists: the file patterns below
// decide what is linted.
export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },
  {
    // Config files (this one included) run in Node, not the browser.
    files: ['**/*.{js,mjs}'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Vite's fast refresh only works when a module exports components.
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // The dependency lists are what keep the SSE hook honest - make it an error.
      'react-hooks/exhaustive-deps': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['src/**/*.test.ts'],
    languageOptions: { globals: globals.node },
  },
)
