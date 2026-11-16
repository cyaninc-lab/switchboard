import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('accounts: add one, use it for a session, remove it', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  await page.getByRole('button', { name: 'Settings' }).click();
  const form = page.getByRole('form', { name: 'Add an account' });
  await form.getByRole('button', { name: 'Add account' }).click();
  await expect(form.getByText('Give it a name and paste a token')).toBeVisible();
  await form.getByLabel('Name').fill('work');
  await form.getByLabel(/^Token/).fill('sk-ant-oat-demo');
  await form.getByRole('button', { name: 'Add account' }).click();
  const list = page.getByRole('list', { name: /Accounts on/ });
  await expect(list).toContainText('work');
  await expect(list).not.toContainText('sk-ant');
  await page.screenshot({ path: 'e2e/.results/accounts.png' });
  await page.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('button', { name: /Untitled session/ }).click();
  await dialog.getByRole('button', { name: 'Add to tree' }).click();
  await page.getByRole('contentinfo').getByLabel('Account').selectOption('work');
  const input = page.getByLabel('Message this session');
  await input.fill('who am i');
  await input.press('Enter');
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(log).toContainText('Done: who am i (token account)');

  await page.getByRole('button', { name: 'Settings' }).click();
  await list.getByRole('button', { name: 'Remove account work' }).click();
  await expect(list).not.toContainText('work');
  await page.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('contentinfo').getByLabel('Account')).toHaveValue('');  // back on the machine login
  await page.getByRole('button', { name: 'Remove from tree' }).click();  // the specs share one demo daemon
});
