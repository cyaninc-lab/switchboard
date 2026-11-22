import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('idle close: off by default, set per machine in Settings, remembered by that machine', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Sessions' })).toBeVisible();

  const settings = page.getByRole('dialog', { name: 'Settings' });
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Sessions' }).click();
  const idle = settings.getByLabel('Close idle sessions on demo');
  await expect(idle).toHaveValue('0');  // Never
  await idle.selectOption({ label: 'After 30 minutes' });
  await page.mouse.click(5, 5);
  await page.reload();
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Sessions' }).click();
  await expect(idle).toHaveValue('30');
  await idle.selectOption({ label: 'Never' });  // the daemon is shared with other specs
  await expect(idle).toHaveValue('0');
});
