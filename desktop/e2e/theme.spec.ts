import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
const base = (page: any) => page.evaluate(() => getComputedStyle(document.querySelector('.app')!).backgroundColor);

test('themes: pick a Catppuccin palette in Settings; it applies everywhere and is remembered', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  // something to look at in the screenshots: the canned session with history
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  const pick = dialog.getByRole('button', { name: /Deploy #52 rolled back/ });
  await expect(pick).toBeVisible();
  const added = !(await pick.isDisabled());  // another spec may already have added it
  if (!added) await dialog.getByRole('button', { name: 'Cancel' }).click();
  else { await pick.click(); await dialog.getByRole('button', { name: 'Add to tree' }).click(); }
  const entry = tree.locator('.entry:not(.sub)').filter({ hasText: /Deploy #52|Shorts deploy/ }).first();
  await entry.click();
  await expect(page.getByRole('log', { name: 'Session history' })).toContainText('Why did the deploy roll back?');

  expect(await base(page)).toBe('rgb(30, 30, 46)');  // Mocha by default
  for (const [name, label, rgb] of [['frappe', 'Frappé', 'rgb(48, 52, 70)'], ['macchiato', 'Macchiato', 'rgb(36, 39, 58)'],
                                    ['mocha', 'Mocha', 'rgb(30, 30, 46)'], ['latte', 'Latte (light)', 'rgb(239, 241, 245)']]) {
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('dialog', { name: 'Settings' }).getByLabel('Theme').selectOption({ label });
    await page.mouse.click(5, 5);  // close the dialog
    expect(await base(page)).toBe(rgb);
    await page.screenshot({ path: `e2e/.results/theme-${name}.png` });
  }
  await page.reload();
  expect(await base(page)).toBe('rgb(239, 241, 245)');  // Latte remembered
  expect(await page.evaluate(() => document.documentElement.style.colorScheme)).toBe('light');
  if (added) { // leave the shared demo daemon as we found it
    await entry.click();
    await page.getByRole('button', { name: 'Remove from tree' }).click();
    await expect(entry).toHaveCount(0);
  }
});
