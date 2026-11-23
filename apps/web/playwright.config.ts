import { defineConfig, devices } from "@playwright/test";

// Happy path against a running stack: `make demo` (API :8000 + web :3000), or set E2E_BASE_URL.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100",
    ...devices["Pixel 7"],
    // system Edge (always present on Windows) avoids downloading a browser; override with PW_CHANNEL
    channel: process.env.PW_CHANNEL ?? "msedge",
  },
});
