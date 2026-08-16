import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8123",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // 开发机已有 Chrome 时直接复用其可执行文件，不要求在发布包中下载浏览器。
    launchOptions: { executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" }
  },
  webServer: {
    command: "uv run dataharness serve --host 127.0.0.1 --port 8123",
    cwd: "..",
    url: "http://127.0.0.1:8123/healthz",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }]
});
