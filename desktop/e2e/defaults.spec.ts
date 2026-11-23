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
  const chip = page.getByRole('button', { name: 'Model, effort, mode and account' });  // the status bar: model · effort · mode · account

  await addNew('Before defaults');
  // "default" says which model it is
  await expect(chip).toContainText('Opus 5.5 · default · machine login');

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
  await expect(chip).toContainText('Haiku 5.5 · plan');
  await tree.getByRole('button', { name: /Before defaults/ }).click();
  await expect(chip).toContainText('Opus 5.5 · default');  // added before: unchanged

  // put the shared demo daemon back
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Sessions' }).click();
  await row.getByRole('combobox', { name: 'Mode', exact: true }).selectOption('');
  await row.getByRole('combobox', { name: 'Model', exact: true }).selectOption('');
  await expect(row.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('');
});

test('right-click a project: a new session in it, and its own defaults', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const dir = mkdtempSync(join(tmpdir(), 'sb-proj-'));
  const proj = dir.split('/').pop()!;
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(dir);
  await dialog.getByLabel('Name in the tree').fill('First in project');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /First in project/ })).toBeVisible();

  await tree.getByRole('button', { name: new RegExp(proj) }).first().click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Defaults…' }).click();
  const pd = page.getByRole('dialog', { name: `Defaults for ${proj}` });
  await Promise.all([page.waitForResponse((r) => r.url().endsWith('/v1/settings') && r.request().method() === 'PUT'),
    pd.getByRole('combobox', { name: 'Model' }).selectOption({ label: 'Haiku 5.5' })]);
  await pd.getByRole('button', { name: 'Done' }).click();

  await tree.getByRole('button', { name: new RegExp(proj) }).first().click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'New session' }).click();
  await expect(dialog.getByLabel(/Folder on/)).toHaveValue(dir);  // where the project works
  await expect(dialog.getByText('Starts with')).toBeVisible();
  await expect(dialog.locator('.startswith')).toContainText('haiku');
  await dialog.getByLabel('Name in the tree').fill('Second in project');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await page.getByRole('button', { name: 'Model, effort, mode and account' }).click(); await expect(page.getByRole('dialog', { name: 'Session settings' }).getByLabel('Model')).toHaveValue('haiku'); await page.getByRole('dialog', { name: 'Session settings' }).getByRole('button', { name: 'Close' }).click();
});
