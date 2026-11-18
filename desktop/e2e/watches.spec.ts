import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('watches: notify and wake-an-agent both land in the inbox', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const add = page.getByRole('form', { name: 'Add to Switchboard' });
  await add.getByRole('button', { name: /Deploy #52 rolled back/ }).click();
  await add.getByRole('button', { name: 'Add to tree' }).click();
  const inbox = page.getByRole('complementary', { name: 'Inbox' });

  // a command watch that just notifies
  await page.getByRole('button', { name: 'Watches' }).click();
  const dlg = page.getByRole('dialog', { name: 'Watches' });
  await dlg.getByRole('button', { name: 'New watch' }).click();
  const form = dlg.getByRole('form', { name: 'Edit watch' });
  await form.getByRole('button', { name: 'Save watch' }).click();
  await expect(form.getByText('Give it a name')).toBeVisible();
  await form.getByLabel('Name').fill('Disk check');
  await form.getByRole('radio', { name: 'Command' }).click();
  await form.getByLabel('Command').fill(`python3 -c "print('disk ok')"`);
  await form.getByRole('button', { name: 'Save watch' }).click();
  await expect(dlg.getByRole('list', { name: /Watches on/ })).toContainText('Disk check');
  await dlg.getByRole('button', { name: 'Test' }).first().click();
  await expect(inbox.getByRole('article', { name: /Disk check/ })).toContainText('disk ok');

  // a schedule that wakes an agent: its reply comes back to the inbox
  await dlg.getByRole('button', { name: 'New watch' }).click();
  await form.getByLabel('Name').fill('Morning check');
  await form.getByLabel('…or daily at').fill('08:00');
  await form.getByRole('radio', { name: 'Wake an agent' }).click();
  await form.getByLabel('Session to wake').selectOption({ label: 'Deploy #52 rolled back' });
  await form.getByLabel('Permission mode while woken').selectOption('auto');
  await form.getByLabel('Instructions for the agent').fill('Summarise anything new.');
  await page.screenshot({ path: 'e2e/.results/watch-form.png' });
  await form.getByRole('button', { name: 'Save watch' }).click();
  const row = dlg.getByRole('listitem').filter({ hasText: 'Morning check' });
  await expect(row).toContainText('wakes Deploy #52 rolled back');
  await expect(row).toContainText('daily at 08:00');
  await row.getByRole('button', { name: 'Test' }).click();
  const reply = inbox.getByRole('article', { name: /Morning check: agent replied/ });
  await expect(reply).toContainText('Summarise anything new.', { timeout: 10000 });  // the fake agent echoes its prompt
  await page.screenshot({ path: 'e2e/.results/inbox.png' });

  // unread count, open the session from the inbox, mark all read
  await expect(page.getByRole('button', { name: /Inbox \(\d+ unread\)/ })).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await reply.getByRole('button', { name: 'Open session' }).click();
  await expect(page.getByText('/srv/agents/shorts-app')).toBeVisible();
  await inbox.getByRole('button', { name: 'Mark all read' }).click();
  await expect(page.getByRole('button', { name: 'Inbox (0 unread)' })).toBeVisible();

  // delete an item (gone from every device)
  const disk = inbox.getByRole('article', { name: /Disk check/ }).first();
  const before = await inbox.getByRole('article').count();
  await disk.getByRole('button', { name: 'Delete' }).click();
  await expect(inbox.getByRole('article')).toHaveCount(before - 1);

  // tidy up (the specs share one demo daemon)
  await page.getByRole('button', { name: 'Watches' }).click();
  for (const name of ['Disk check', 'Morning check']) await dlg.getByRole('button', { name: `Delete ${name}` }).click();
  await expect(dlg.getByText('No watches on demo yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Remove from tree' }).click();
});
