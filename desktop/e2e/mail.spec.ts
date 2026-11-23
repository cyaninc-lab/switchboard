import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('messages between sessions show in their own box, in both chats', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Mail B', 'Mail A']) {  // A last, so it's the one open
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const log = page.getByRole('log', { name: 'Session history' });
  const input = page.getByLabel('Message this session');

  // A sends: its step is a "To Mail B" box
  await input.fill('tell Mail B: is the backup finished?');
  await input.press('Enter');
  const sent = log.getByRole('note', { name: 'To Mail B' });
  await expect(sent).toContainText('is the backup finished?');
  await expect(log).toContainText('Delivered to "Mail B"');

  // B received it as a "From Mail A" box, not as the raw prompt
  await tree.getByRole('button', { name: /Mail B/ }).click();
  const got = log.getByRole('note', { name: 'From Mail A' });
  await expect(got).toContainText('is the backup finished?');
  await expect(log.locator('.you', { hasText: 'sent through Switchboard' })).toHaveCount(0);  // not shown as typed (the demo's reply echoes it)
  // and on the right, the two agents' conversation
  const side = page.getByRole('complementary', { name: 'Between agents' });
  await expect(side.getByRole('log', { name: 'Letters with Mail A' })).toContainText('is the backup finished?');
  await side.getByRole('button', { name: "Hide the agents' chat" }).click();
  await expect(side).toHaveCount(0);
  await page.getByRole('button', { name: '✉ Agents' }).click();
  await expect(side).toBeVisible();
  await page.screenshot({ path: 'e2e/.results/mail.png' });  // the demo's trigger words (run, plan, slow…) are avoided above
});
