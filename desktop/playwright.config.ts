import { defineConfig } from '@playwright/test';

// Headless UI tests: the real Svelte UI against a daemon in demo mode (canned sessions, no Claude usage).
// SB_DAEMON = path to the switchboard-daemon executable (default: the repo's dev venv).
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
export const DAEMON_PORT = 7891;
export const DATA = 'e2e/.data';

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'e2e/.results',
  workers: 1, // the tests share one demo daemon
  // ponytail: one retry on CI to capture a trace of an intermittent failure (new.spec once, not reproducible locally); drop once explained
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: 'http://localhost:1421', headless: true, viewport: { width: 1280, height: 820 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: [
    { command: `rm -rf ${DATA} && ${daemon} --data ${DATA} serve --demo --port ${DAEMON_PORT}`, url: `http://127.0.0.1:${DAEMON_PORT}/v1/machine`, reuseExistingServer: false, ignoreHTTPSErrors: true },
    { command: 'npx vite dev --port 1421 --strictPort', url: 'http://localhost:1421', reuseExistingServer: false },
  ],
});
