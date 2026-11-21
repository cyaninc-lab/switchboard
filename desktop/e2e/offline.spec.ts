import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test("a machine it can't reach is marked, with a Retry that recovers", async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await expect(tree.locator('.machine')).toBeVisible();
  await expect(tree.locator('.offtag')).toHaveCount(0);
  await page.route('**/v1/entries', (r) => r.abort('connectionrefused'));  // as with the machine off, or this device off its tailnet
  await page.reload();
  await expect(tree.locator('.offtag')).toHaveText("can't reach");
  await expect(tree.locator('.offnote')).toContainText("isn't answering");
  await page.unroute('**/v1/entries');
  await tree.getByRole('button', { name: 'Retry' }).click();
  await expect(tree.locator('.offtag')).toHaveCount(0);
});
