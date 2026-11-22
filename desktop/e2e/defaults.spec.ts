import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('defaults for new sessions in Settings; the footer says what "default" is', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const addNew = async (name: string) => {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-def-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  };
  const model = page.getByRole('combobox', { name: 'Model' }), mode = page.getByRole('combobox', { name: 'Permission mode' });

  await addNew('Before defaults');
  // "default" says which model it is
  await expect(model.locator('option[value="default"]')).toHaveText('Default · Opus 5.5');

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Sessions' }).click();
  const row = page.getByRole('dialog', { name: 'Settings' }).getByRole('group', { name: 'New sessions on demo' });
  // each change is saved by the daemon: wait for that, or the next session can start before it lands
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/v1/settings') && r.request().method() === 'PUT');
  await Promise.all([saved(), row.getByRole('combobox', { name: 'Mode', exact: true }).selectOption('plan')]);
  await Promise.all([saved(), row.getByRole('combobox', { name: 'Model', exact: true }).selectOption({ label: 'Haiku 5.5' })]);
  await expect(row.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('haiku');
  await page.screenshot({ path: 'e2e/.results/defaults.png' });
  await page.mouse.click(5, 5);

  await addNew('After defaults');
  await expect(mode).toHaveValue('plan');
  await expect(model).toHaveValue('haiku');
  await tree.getByRole('button', { name: /Before defaults/ }).click();
  await expect(mode).toHaveValue('default');  // added before: unchanged
  await expect(model).toHaveValue('default');

  // put the shared demo daemon back
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Sessions' }).click();
  await row.getByRole('combobox', { name: 'Mode', exact: true }).selectOption('');
  await row.getByRole('combobox', { name: 'Model', exact: true }).selectOption('');
  await expect(row.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('');
});
