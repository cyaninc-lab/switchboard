import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('background tasks: a bar above the message box lists them, with Stop; a count in the tree', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-bg-')));
  await dialog.getByLabel('Name in the tree').fill('Background jobs');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  const entry = tree.getByRole('button', { name: /Background jobs/ });
  await expect(entry).toBeVisible();
  const input = page.getByLabel('Message this session');
  await input.fill('start the long background build');
  await input.press('Enter');

  // the slim bar shows only what's running, and opens the list
  const bar = page.getByRole('region', { name: 'Background tasks' });
  await expect(bar).toContainText('2 running');
  await expect(entry.locator('.bgbadge')).toHaveText('⟳ 2');
  await expect(bar).toContainText('1 running');  // the build finishes by itself; the dev server keeps going
  await bar.getByRole('button', { name: /running/ }).click();
  const sheet = page.getByRole('dialog', { name: 'Background tasks' });
  const running = sheet.getByRole('list', { name: 'Running' }), finished = sheet.getByRole('list', { name: 'Finished' });
  await expect(running).toContainText('npm run dev');
  await expect(finished).toContainText('npm run build');
  await expect(finished).toContainText('Build finished, 0 warnings');  // Claude Code's summary
  await expect(finished.locator('code.path')).toHaveText('/tmp/demo-bg1.output');
  await page.screenshot({ path: 'e2e/.results/background.png' });
  // stop the dev server: the bar goes (nothing running), the badge too
  await running.getByRole('button', { name: 'Stop' }).click();
  await expect(finished).toContainText('npm run dev');
  await expect(sheet.getByText('Nothing running.')).toBeVisible();
  await sheet.getByRole('button', { name: 'Close' }).click();
  await expect(bar).toHaveCount(0);
  await expect(entry.locator('.bgbadge')).toHaveCount(0);
  // finished ones stay reachable from ⋯ in the session's header
  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: /Background tasks/ }).click();
  await expect(finished).toContainText('npm run build');
  await expect(finished).toContainText('npm run dev');
});
