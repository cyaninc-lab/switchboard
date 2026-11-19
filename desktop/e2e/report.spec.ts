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
  await dialog.getByRole('button', { name: 'Open on GitHub instead' }).click();
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

test('report a bug: Send to GitHub signs in once (device code), files the issue and links it', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');
  // stand in for GitHub at the daemon's /v1/github endpoints (the daemon side has its own test against a fake GitHub)
  let signedIn = false, polls = 0, filed: any = null;
  const d = `http://127.0.0.1:${DAEMON_PORT}/v1/github`;
  await page.route(d, (r) => r.fulfill({ json: { signed_in: signedIn, login: signedIn ? 'someone' : null } }));
  await page.route(`${d}/device`, (r) => r.fulfill({ json: { device_code: 'dc', user_code: 'WXYZ-9876', verification_uri: 'https://github.com/login/device', expires_in: 900, interval: 1 } }));
  await page.route(`${d}/poll`, (r) => { polls++; if (polls > 1) signedIn = true; return r.fulfill({ json: polls > 1 ? { status: 'done', login: 'someone' } : { status: 'pending' } }); });
  await page.route(`${d}/issue`, async (r) => { filed = r.request().postDataJSON(); return r.fulfill({ json: { url: 'https://github.com/cyaninc-lab/switchboard/issues/123', number: 123 } }); });
  await page.context().route('https://github.com/**', (r) => r.fulfill({ contentType: 'text/html', body: '<title>stub</title>' }));

  await page.getByRole('button', { name: 'Report a bug' }).click();
  const dialog = page.getByRole('form', { name: 'Report a bug' });
  await dialog.getByLabel(/What happened/).fill('Tabs vanish\nAfter a restart my tabs were gone.');
  const popup = page.waitForEvent('popup');
  await dialog.getByRole('button', { name: 'Send to GitHub' }).click();
  // first time: the code to enter on GitHub, copied, and GitHub's page opened
  await expect(dialog.getByRole('status', { name: 'GitHub sign-in' })).toContainText('WXYZ-9876');
  expect((await popup).url()).toContain('github.com/login/device');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('WXYZ-9876');
  await page.screenshot({ path: 'e2e/.results/report-signin.png' });
  // approved: the issue is filed with the report, and linked
  await expect(dialog).toContainText('Sent as issue #123');
  expect(filed.title).toBe('Tabs vanish');
  expect(filed.body).toContain('After a restart my tabs were gone.');
  expect(filed.body).toContain('### Daemon log: demo');
  await expect(dialog).toContainText('Sends as someone');
  // next time: no sign-in step
  polls = 100;
  await dialog.getByLabel(/What happened/).fill('Another one');
  await dialog.getByRole('button', { name: 'Send to GitHub' }).click();
  await expect(dialog).toContainText('Sent as issue #123');
  await expect(dialog.getByRole('status', { name: 'GitHub sign-in' })).toHaveCount(0);
  expect(filed.title).toBe('Another one');
});
