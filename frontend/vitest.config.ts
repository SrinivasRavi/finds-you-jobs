// Unit-test runner config (vitest). Deliberately standalone — vitest would
// otherwise pick up vite.config.ts and drag the tailwind/react dev-server
// plugins into test startup. Coexists with Playwright: only src/**/*.test.*
// files are unit tests; e2e/ stays exclusively Playwright's (`*.spec.ts`).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
