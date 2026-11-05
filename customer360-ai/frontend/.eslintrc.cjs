// ESLint 8 flat-less config. The `lint` script has existed since the frontend was
// scaffolded but had no config file, so it failed on every run - a check that is
// never run is not a check. Kept to eslint:recommended plus the TypeScript
// recommended set: rules that catch defects, not stylistic preferences the
// formatter/prettier debate should own.
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } },
  plugins: ["@typescript-eslint"],
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended"],
  ignorePatterns: ["dist", "node_modules", "coverage", "*.cjs", "vite.config.ts", "playwright.config.ts", "playwright-report"],
  rules: {
    // TypeScript resolves these itself and reports them through `tsc --noEmit`,
    // which the build already runs; duplicating them here only adds noise.
    "no-undef": "off",
    "no-unused-vars": "off",
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
    ],
    // Unused disable directives are reported by the --report-unused-disable-directives
    // flag in the lint script; an empty catch is usually deliberate degradation.
    "no-empty": ["error", { allowEmptyCatch: true }],
  },
  overrides: [
    {
      // Browser specs and vitest suites both run outside the app.
      files: ["src/__tests__/**/*.{ts,tsx}", "**/*.test.{ts,tsx}", "e2e/**/*.{ts,tsx}"],
      env: { node: true },
      rules: { "@typescript-eslint/no-explicit-any": "off" },
    },
  ],
};
