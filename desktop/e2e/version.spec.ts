import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';
import pkg from '../package.json' with { type: 'json' };

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('a machine running a different Switchboard says so on its chip', async ({ page }) => {
  // the demo daemon is this version; pretend it's an older one, then a newer one
  let fake = '0.1.8';
  await page.route(`http://127.0.0.1:${DAEMON_PORT}/v1/machine`, async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), daemon: fake } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const chip = page.locator('.chip', { hasText: 'demo' });
  await expect(chip.locator('.vertag')).toHaveText('older: 0.1.8');
  await expect(chip.locator('.vertag')).toHaveAttribute('title', new RegExp(`demo runs Switchboard 0.1.8; this app is ${pkg.version}`));
  await page.screenshot({ path: 'e2e/.results/version-older.png', clip: { x: 0, y: 0, width: 1280, height: 60 } });
  fake = '9.0.0';
  await page.reload();
  await expect(chip.locator('.vertag')).toHaveText('newer: 9.0.0');
  fake = pkg.version;
  await page.reload();
  await expect(chip).toContainText('online');
  await expect(chip.locator('.vertag')).toHaveCount(0);
});
