import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('/btw: a side question opens a panel on the right; the session never sees it; kept per session; × closes it', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Btw main', 'Btw other']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-btw-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  await tree.getByRole('button', { name: /Btw main/ }).click();
  const input = page.getByLabel('Message this session');
  const log = page.getByRole('log', { name: 'Session history' });
  await input.fill('hello there');
  await input.press('Enter');
  await expect(log).toContainText('Done: hello there');

  // the slash menu offers it
  await input.fill('/bt');
  await expect(page.getByRole('listbox', { name: 'Slash commands' })).toContainText('/btw');
  await input.fill('/btw which file has the bug?');
  await input.press('Enter');
  const side = page.getByRole('complementary', { name: 'Side chat' });
  await expect(side.getByRole('log', { name: 'Side chat answers' })).toContainText('Side answer: which file has the bug?');
  await expect(log).not.toContainText('which file has the bug');  // not in the session's chat
  await expect(input).toHaveValue('');
  // a follow-up in the panel
  await side.getByLabel('Side question').fill('and why?');
  await side.getByLabel('Side question').press('Enter');
  await expect(side).toContainText('Side answer: and why?');
  await page.screenshot({ path: 'e2e/.results/btw.png' });
  // kept per session: another session doesn't show it, coming back does
  await tree.getByRole('button', { name: /Btw other/ }).click();
  await expect(side).toHaveCount(0);
  await tree.getByRole('button', { name: /Btw main/ }).click();
  await expect(side).toContainText('Side answer: and why?');
  // × closes it
  await side.getByRole('button', { name: 'Close side chat' }).click();
  await expect(side).toHaveCount(0);
  // a brand-new session has nothing to ask about yet
  await tree.getByRole('button', { name: /Btw other/ }).click();
  await input.fill('/btw anything?');
  await input.press('Enter');
  await expect(side).toContainText('Say something in this session first');
});
