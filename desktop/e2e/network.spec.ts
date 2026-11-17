import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('other devices: reachable over Tailscale and pair from the app', async ({ page, request }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');

  await page.getByRole('button', { name: 'Settings' }).click();
  const dlg = page.getByRole('dialog', { name: 'Settings' });
  const toggle = dlg.getByRole('checkbox', { name: /Reachable from my other devices over Tailscale/ });
  await expect(dlg).toContainText('demo-box.tail.ts.net on your tailnet');
  await toggle.check();
  await expect(dlg).toContainText(`Reachable at http://demo-box:${DAEMON_PORT}`);
  // the daemon really answers on the (fake) tailnet address now
  expect((await request.get(`http://127.0.0.2:${DAEMON_PORT}/v1/machine`)).status()).toBe(401);
  await dlg.getByRole('button', { name: 'Pair another device' }).click();
  const box = dlg.getByRole('status').filter({ hasText: 'On the other device' });
  await expect(box).toContainText(`http://demo-box:${DAEMON_PORT}`);
  const code = (await box.locator('.big').textContent())!.trim();
  expect(code).toMatch(/^[A-Z2-9]{8}$/);
  await page.screenshot({ path: 'e2e/.results/other-devices.png' });
  // the code works for a second client
  const r = await request.post(`http://127.0.0.1:${DAEMON_PORT}/v1/pair`, { data: { code, client_name: 'other device' } });
  expect(r.status()).toBe(200);
  await toggle.uncheck();
  await expect(dlg).not.toContainText('Reachable at');
});
