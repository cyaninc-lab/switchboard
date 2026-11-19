import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('Removed list: a removed session keeps its name and project; restore it, or delete it for good from there', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Archive me', 'Archive and delete']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-ar-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const remove = async (name: string) => {
    await tree.getByRole('button', { name: new RegExp(name) }).click({ button: 'right' });
    await page.getByRole('menu', { name: `${name} actions` }).getByRole('menuitem', { name: 'Remove from tree' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toHaveCount(0);
  };
  await remove('Archive me');
  await remove('Archive and delete');

  await page.getByRole('button', { name: 'Removed', exact: true }).click();
  const box = page.getByRole('dialog', { name: 'Removed sessions' });
  const row = (n: string) => box.getByRole('listitem').filter({ hasText: n });
  await expect(row('Archive me')).toContainText('removed');
  await page.screenshot({ path: 'e2e/.results/removed.png' });
  await row('Archive me').getByRole('button', { name: 'Restore' }).click();
  await expect(row('Archive me')).toHaveCount(0);
  await expect(tree.getByRole('button', { name: /Archive me/ })).toBeVisible();  // back, same name

  await row('Archive and delete').getByRole('button', { name: 'Delete for good…' }).click();
  const confirm = page.getByRole('form', { name: 'Delete for good' });
  await confirm.getByLabel("Type the session's name to confirm").fill('Archive and delete');
  await confirm.getByRole('button', { name: 'Delete for good' }).click();
  await expect(page.locator('.toast')).toContainText('Deleted Archive and delete');
  await page.getByRole('button', { name: 'Removed', exact: true }).click();
  await expect(row('Archive and delete')).toHaveCount(0);
});
