import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('/clear empties the chat and stays cleared after a reload (the entry follows the new session)', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-clear-')));
  await dialog.getByLabel('Name in the tree').fill('Clear me');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Clear me/ })).toBeVisible();
  const input = page.getByLabel('Message this session');
  const log = page.getByRole('log', { name: 'Session history' });
  const say = async (t: string) => { await input.fill(t); await input.press('Enter'); };
  await say('remember magenta');
  await expect(log).toContainText('Done: remember magenta');
  await say('/clear');
  await expect(log).toContainText('Conversation cleared');
  await expect(log).not.toContainText('remember magenta');
  await say('after the clear');
  await expect(log).toContainText('Done: after the clear');
  await page.reload();
  await tree.getByRole('button', { name: /Clear me/ }).click();
  await expect(log).toContainText('after the clear');
  await expect(log).not.toContainText('remember magenta');
});
