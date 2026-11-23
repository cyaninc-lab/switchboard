import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('a popped-out window shows just its session, with the draft handed over', async ({ page }) => {
  const url = `http://127.0.0.1:${DAEMON_PORT}`;
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(url);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
  await dialog.getByLabel('Name in the tree').fill('Solo one');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Solo one/ })).toBeVisible();
  // outside the desktop app there's no Pop out button: fake what popOut does (hand over the draft, open the URL)
  await expect(page.getByRole('button', { name: 'Pop out' })).toHaveCount(0);
  const id = await page.evaluate(async (url) => {
    const m = JSON.parse(localStorage.getItem('switchboard.machines')!).find((x: any) => x.url === url);
    const r = await (await fetch(`${url}/v1/entries`, { headers: { Authorization: `Bearer ${m.token}` } })).json();
    const e = r.entries.find((x: any) => x.name === 'Solo one');
    localStorage.setItem('switchboard.handoff.' + e.id, JSON.stringify({ url, entry: e.id, text: 'carried over', images: [] }));
    return e.id;
  }, url);
  const tabsBefore = await page.evaluate(() => localStorage.getItem('switchboard.tabs'));
  await page.goto(`/?machine=${encodeURIComponent(url)}&solo=${id}`);
  await expect(page.getByLabel('Message this session')).toHaveValue('carried over');
  await expect(page.getByRole('button', { name: 'Keep on top' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Back to tabs' })).toHaveCount(0);  // the window's own ✕ does that
  // just the session: no tree, tabs, inbox or title bar
  await expect(tree).toBeHidden();
  await expect(page.getByRole('tablist', { name: 'Open sessions' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '+ Connect a machine' })).toBeHidden();
  await expect(page.getByText('Inbox', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'e2e/.results/window.png' });
  // the handover is used once, and the main window's tabs aren't touched
  expect(await page.evaluate((id) => localStorage.getItem('switchboard.handoff.' + id), id)).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('switchboard.tabs'))).toBe(tabsBefore);
  // it works like any session
  const input = page.getByLabel('Message this session');
  await input.fill('hello from the window');
  await input.press('Enter');
  await expect(page.getByRole('log', { name: 'Session history' })).toContainText('Done: hello from the window');
});
