import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('Up in an empty box brings back your last message', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill('/tmp');
  await dialog.getByLabel('Name in the tree').fill('Keys');
  await dialog.getByRole('button', { name: 'Create session' }).click();

  const box = page.getByLabel('Message this session');
  await box.fill('say it again please');
  await box.press('Enter');
  await expect(page.getByRole('log').getByText('say it again please')).toBeVisible();
  await expect(box).toHaveValue('');
  await box.press('ArrowUp');
  await expect(box).toHaveValue('say it again please');
  await box.press('ArrowUp');  // not empty any more: the caret moves, nothing else
  await expect(box).toHaveValue('say it again please');
});
