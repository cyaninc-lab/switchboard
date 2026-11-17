import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('chat: stream, approve, deny, interrupt, auto mode, stop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('button', { name: /Investigate slow feed/ }).click();
  await dialog.getByRole('button', { name: 'Add to tree' }).click();

  const log = page.getByRole('log', { name: 'Session history' });
  const input = page.getByLabel('Message this session');
  const entry = page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: /Investigate slow feed/ });

  // approve
  await input.fill('please run the checks');
  await input.press('Enter');
  await expect(log).toContainText('please run the checks');
  const card = page.getByRole('group', { name: 'Permission needed' });
  await expect(card).toContainText('Bash: git status');
  await expect(entry).toHaveAttribute('title', 'Waiting for you');
  await page.screenshot({ path: 'e2e/.results/permission.png' });
  await card.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(log).toContainText('nothing to commit');
  await expect(log).toContainText('Done: please run the checks');
  await expect(entry).toHaveAttribute('title', 'Live and ready');

  // deny
  await input.fill('run it again');
  await input.press('Enter');
  await card.getByRole('button', { name: 'Deny' }).click();
  await expect(log).toContainText('Permission denied');

  // interrupt a slow task with Esc
  await expect(entry).toHaveAttribute('title', 'Live and ready');
  await input.fill('a slow job');
  await input.press('Enter');
  await expect(page.getByRole('button', { name: 'Esc · interrupt' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(entry).toHaveAttribute('title', 'Live and ready', { timeout: 5000 });

  // auto mode: no prompt
  await page.getByRole('contentinfo').getByLabel('Permission mode').selectOption('auto');
  await input.fill('run in auto');
  await input.press('Enter');
  await expect(log).toContainText('Done: run in auto');
  await expect(card).toHaveCount(0);

  // stop closes the live session; it stays in the tree
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(entry).toHaveAttribute('title', /Stopped/);
  await page.screenshot({ path: 'e2e/.results/chat.png' });
});
