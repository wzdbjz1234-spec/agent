import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期只代理本地 FastAPI，生产构建不包含任何 API 地址或秘密。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/projects": "http://127.0.0.1:8000",
      "/tasks": "http://127.0.0.1:8000",
      "/findings": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
      "/readyz": "http://127.0.0.1:8000",
      "/diagnostics": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000"
    }
  },
  build: {
    sourcemap: false,
    outDir: "dist",
    emptyOutDir: true
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    globals: true,
    include: ["src/test/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**"]
  }
});
