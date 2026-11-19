import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('session tree folds away: button at any width, by itself when narrow, sun on the button while folded', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await expect(tree).toBeVisible();

  // a fresh session that will wait on a permission
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
  await dialog.getByLabel('Name in the tree').fill('Side A');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Side A/ })).toBeVisible();

  // wide window: the button hides and shows it, and the choice is remembered
  await page.getByRole('button', { name: 'Hide sessions' }).click();
  await expect(tree).toBeHidden();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Show sessions' })).toBeVisible();
  await expect(tree).toBeHidden();
  await page.getByRole('button', { name: 'Show sessions' }).click();
  await expect(tree).toBeVisible();
  await tree.getByRole('button', { name: /Side A/ }).click();

  // narrow window folds it by itself; the button still opens it; wide again brings back the user's choice
  await page.setViewportSize({ width: 800, height: 900 });
  await expect(tree).toBeHidden();
  const show = page.getByRole('button', { name: 'Show sessions' });
  // while folded, a session waiting on you shows its sun on the button
  const input = page.getByLabel('Message this session');
  await input.fill('please run the checks');
  await input.press('Enter');
  await expect(show.locator('.st.waiting')).toBeVisible();
  await page.screenshot({ path: 'e2e/.results/sidebar-folded.png' });
  await show.click();
  await expect(tree).toBeVisible();
  await page.setViewportSize({ width: 1400, height: 900 });
  await expect(tree).toBeVisible();
  await page.getByRole('group', { name: 'Permission needed' }).getByRole('button', { name: 'Allow', exact: true }).click();
});
