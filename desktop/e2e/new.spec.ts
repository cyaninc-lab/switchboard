import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('create a new session in a folder', async ({ page }) => {
  const folder = mkdtempSync(join(tmpdir(), 'sb-e2e-'));
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  // the suggested folder arrives late (as on a slow machine) while the folder is being typed: it must not land in it
  await page.route('**/v1/folders', async (r) => { await new Promise((ok) => setTimeout(ok, 1500)); await r.continue(); });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).click(); // in the field before the suggestion arrives...
  await page.waitForTimeout(1800);
  await dialog.getByLabel(/Folder on/).pressSequentially('/no/such/folder'); // ...then typing must not join it
  await expect(dialog.getByLabel(/Folder on/)).toHaveValue('/no/such/folder');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(dialog.getByText('No folder /no/such/folder')).toBeVisible();
  await dialog.getByLabel(/Folder on/).fill(folder);
  await dialog.getByLabel('Name in the tree').fill('Brand new');
  await page.screenshot({ path: 'e2e/.results/new-session.png' });
  await dialog.getByRole('button', { name: 'Create session' }).click();

  const entry = page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: /Brand new/ });
  await expect(entry).toHaveAttribute('title', /^New/);
  await expect(page.getByText(folder)).toBeVisible();
  const input = page.getByLabel('Message this session');
  await input.fill('first words');
  await input.press('Enter');
  await expect(page.getByRole('log', { name: 'Session history' })).toContainText('Done: first words');
  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Close session' }).click();
  await expect(entry).toHaveAttribute('title', /Stopped/);  // it exists now: an ordinary session
});
