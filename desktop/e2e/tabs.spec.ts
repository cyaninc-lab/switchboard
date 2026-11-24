import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('tabs: preview tab replaced by the next click, kept by sending or Ctrl+click; keys, close, reorder, remembered', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Tab A', 'Tab B', 'Tab C']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const bar = page.getByRole('tablist', { name: 'Open sessions' });
  const tabs = bar.getByRole('tab');
  const names = async () => (await tabs.allTextContents()).map((t) => t.trim()).filter((t) => t.startsWith('Tab '));
  const entry = (n: string) => tree.getByRole('button', { name: new RegExp(n) });
  // creating a session opened it; whatever tabs other specs left, start from a clean bar
  while (await bar.getByRole('button', { name: /^Close tab/ }).count()) await bar.getByRole('button', { name: /^Close tab/ }).first().click();
  await expect(bar).toHaveCount(0);

  // a click opens a preview tab; the next click replaces it
  await entry('Tab A').click();
  expect(await names()).toEqual(['Tab A']);
  await expect(bar.locator('.tab.preview')).toHaveCount(1);
  await entry('Tab B').click();
  expect(await names()).toEqual(['Tab B']);
  // sending keeps it, and keeps the draft per tab
  const input = page.getByLabel('Message this session');
  await input.fill('hello there');
  await input.press('Enter');
  await expect(bar.locator('.tab.preview')).toHaveCount(0);
  await entry('Tab A').click();
  expect(await names()).toEqual(['Tab B', 'Tab A']);
  await input.fill('a draft for A');
  // Ctrl+click opens a kept tab next to the current one
  await entry('Tab C').click({ modifiers: ['Control'] });
  expect(await names()).toEqual(['Tab B', 'Tab A', 'Tab C']);
  await expect(bar.locator('.tab.preview')).toHaveCount(1);  // A is still the preview
  await bar.getByRole('tab', { name: 'Tab A' }).dblclick();
  await expect(bar.locator('.tab.preview')).toHaveCount(0);
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab A');
  await expect(input).toHaveValue('a draft for A');  // the draft came back with its tab

  // Ctrl+Tab / Ctrl+Shift+Tab cycle
  await page.locator('body').press('Control+Tab');
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab C');
  await expect(input).toHaveValue('');
  await page.locator('body').press('Control+Tab');
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab B');
  await page.locator('body').press('Control+Shift+Tab');
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab C');

  // middle button going down is taken (Windows would start autoscroll); the bar never scrolls vertically
  const prevented = (sel: string) => page.locator(sel).first().evaluate((el) => !el.dispatchEvent(new MouseEvent('mousedown', { button: 1, bubbles: true, cancelable: true })));
  expect(await prevented('.tabs .tab')).toBe(true);
  expect(await prevented('nav.tree button.entry')).toBe(true);
  expect(await bar.evaluate((el) => el.scrollHeight <= el.clientHeight)).toBe(true);

  // drag to reorder
  await bar.locator('.tab', { hasText: 'Tab C' }).dragTo(bar.locator('.tab', { hasText: 'Tab B' }));
  expect(await names()).toEqual(['Tab C', 'Tab B', 'Tab A']);
  await page.screenshot({ path: 'e2e/.results/tabs.png' });

  // remembered across a restart, with the active one
  await page.reload();
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab C');
  expect(await names()).toEqual(['Tab C', 'Tab B', 'Tab A']);

  // Ctrl+W closes the active tab and shows its neighbour; × and middle-click close too
  await page.locator('body').press('Control+w');
  expect(await names()).toEqual(['Tab B', 'Tab A']);
  await expect(bar.getByRole('tab', { selected: true })).toHaveText('Tab B');
  await bar.locator('.tab', { hasText: 'Tab A' }).click({ button: 'middle' });
  expect(await names()).toEqual(['Tab B']);
  await bar.getByRole('button', { name: 'Close tab Tab B' }).click();
  await expect(bar).toHaveCount(0);
  await expect(page.getByLabel('Message this session')).toHaveCount(0);
});

test('closing a session from its right-click menu closes its tab too', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Close me', 'Stay open']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    const box = page.getByLabel('Message this session');
    await box.fill('hello'); await box.press('Enter');  // live, and its tab kept
    await expect(page.getByRole('log', { name: 'Session history' }).getByText('hello').first()).toBeVisible();
  }
  const tabs = page.getByRole('tablist', { name: 'Open sessions' });
  await expect(tabs.getByRole('tab', { name: 'Close me' })).toBeVisible();
  await tree.getByRole('button', { name: /Close me/ }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Close session' }).click();
  await expect(tabs.getByRole('tab', { name: 'Close me' })).toHaveCount(0);
  await expect(tabs.getByRole('tab', { name: 'Stay open' })).toBeVisible();
});
