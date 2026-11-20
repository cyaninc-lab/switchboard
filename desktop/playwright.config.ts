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
  use: { baseURL: 'http://localhost:1421', headless: true, viewport: { width: 1280, height: 820 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: [
    // SWITCHBOARD_WEBUI: the daemon serves the app the second server builds (CI installs the daemon as a package, away from desktop/build)
    { command: `rm -rf ${DATA} && SWITCHBOARD_WEBUI=${process.cwd()}/build ${daemon} --data ${DATA} serve --demo --port ${DAEMON_PORT}`, url: `http://127.0.0.1:${DAEMON_PORT}/v1/machine`, reuseExistingServer: false, ignoreHTTPSErrors: true },
    // the production build, not the dev server: Vite's dev server reloads the page when it first meets a dependency,
    // which wiped open dialogs mid-test (the intermittent CI failure)
    { command: 'npx vite build --logLevel error && npx vite preview --port 1421 --strictPort', url: 'http://localhost:1421', reuseExistingServer: false, timeout: 120000 },
  ],
});
