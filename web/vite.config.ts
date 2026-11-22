import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Python API runs separately (`uv run skeptic serve`);
// Vite forwards /api to it. In production FastAPI serves the built files.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Keep the browser's Host header (Vite's string shorthand would rewrite it):
      // the API refuses writes whose Origin does not match their Host.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
});
