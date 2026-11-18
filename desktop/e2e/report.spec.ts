import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('report a bug: copies a scrubbed report and opens a pre-filled issue', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  page.on('console', (m) => m.type() === 'error' && console.log('CONSOLE', m.text()));
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');
  await page.evaluate(() => console.error('something broke, token sk-ant-oat01-SECRETSECRET1234'));

  await page.getByRole('button', { name: 'Report a bug' }).click();
  const dialog = page.getByRole('form', { name: 'Report a bug' });
  await dialog.getByLabel(/What happened/).fill('Tree flashes offline\nAfter switching to the background service the chip says offline.');
  // answer GitHub with a stub: the test checks the address the app opens, not GitHub's own redirects
  // (a public repo sends logged-out visitors to its login page) or the network
  await page.context().route('https://github.com/**', (r) => r.fulfill({ contentType: 'text/html', body: '<title>stub</title>' }));
  const popup = page.waitForEvent('popup');
  await dialog.getByRole('button', { name: /Copy report/ }).click();
  const url = (await popup).url();
  expect(url).toContain('github.com/cyaninc-lab/switchboard/issues/new');
  expect(decodeURIComponent(url)).toContain('title=Tree flashes offline');
  await expect(dialog.getByRole('status')).toContainText('Report copied');

  const report = await page.evaluate(() => navigator.clipboard.readText());
  expect(report).toContain('After switching to the background service');
  expect(report).toMatch(/demo: daemon \S+, Agent SDK demo/);
  expect(report).toContain('something broke, token sk-ant-[redacted]');
  expect(report).toContain('### Daemon log: demo');
  expect(report).not.toContain('SECRETSECRET');
  await page.screenshot({ path: 'e2e/.results/report.png' });
});
