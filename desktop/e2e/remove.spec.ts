import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('right-click: Remove from tree, and a tucked-away Delete for good that needs the name typed', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Keep me around', 'Gone for good']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-rm-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const menuFor = async (name: string) => {
    await tree.getByRole('button', { name: new RegExp(name) }).click({ button: 'right' });
    return page.getByRole('menu', { name: `${name} actions` });
  };

  // Remove from tree: off the list (and its tab closes)
  let menu = await menuFor('Keep me around');
  await menu.getByRole('menuitem', { name: 'Remove from tree' }).click();
  await expect(tree.getByRole('button', { name: /Keep me around/ })).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: 'Open sessions' }).getByRole('tab', { name: /Keep me around/ })).toHaveCount(0);

  // Delete for good: last in the menu, behind a confirmation that wants the exact name
  menu = await menuFor('Gone for good');
  const items = menu.getByRole('menuitem');
  await expect(items.last()).toHaveText('Delete for good…');
  await items.last().click();
  const confirm = page.getByRole('form', { name: 'Delete for good' });
  await expect(confirm).toContainText("can't be undone");
  const go = confirm.getByRole('button', { name: 'Delete for good' });
  await expect(go).toBeDisabled();
  await confirm.getByLabel("Type the session's name to confirm").fill('Gone for goo');
  await expect(go).toBeDisabled();
  await page.screenshot({ path: 'e2e/.results/delete-confirm.png' });
  await confirm.getByLabel("Type the session's name to confirm").fill('Gone for good');
  await go.click();
  await expect(confirm).toHaveCount(0);
  await expect(tree.getByRole('button', { name: /Gone for good/ })).toHaveCount(0);
  await expect(page.locator('.toast')).toContainText('Deleted Gone for good');
});
