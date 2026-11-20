import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { pairLink, parsePairLink } from '../src/lib/shell';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('the pairing QR holds the address and the code, as a normal web address', () => {
  const link = pairLink('http://fantome.tail1234.ts.net:7878', 'K7QM2XRA');
  expect(link).toBe('http://fantome.tail1234.ts.net:7878/?pair=K7QM2XRA');
  expect(parsePairLink(link)).toEqual({ url: 'http://fantome.tail1234.ts.net:7878', code: 'K7QM2XRA' });
  expect(parsePairLink('https://example.com/')).toBeNull();
  expect(parsePairLink('not a link')).toBeNull();
});

test('opening the QR code\'s address in a browser (any phone camera) pairs with that machine at once', async ({ page }) => {
  const code = execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim();
  await page.goto(`http://127.0.0.1:${DAEMON_PORT}/?pair=${code}`);  // the daemon serves the app itself
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');
  expect(new URL(page.url()).search).toBe('');  // the single-use code is taken out of the address bar
  await expect(page.getByRole('navigation', { name: 'Sessions' })).toBeVisible();
});
