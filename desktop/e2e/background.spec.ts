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

  const bar = page.getByRole('region', { name: 'Background tasks' });
  await expect(bar).toContainText('2 running');
  await expect(entry.locator('.bgbadge')).toHaveText('⟳ 2');
  // the build finishes by itself (with Claude Code's summary); the dev server keeps going
  await expect(bar).toContainText('1 running');
  await expect(bar).toContainText('1 finished');
  await bar.getByRole('button', { name: /running/ }).click();  // open the list
  const rows = bar.locator('.bgrow');
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: 'npm run build' })).toContainText('Build finished, 0 warnings');
  await expect(rows.filter({ hasText: 'npm run build' }).locator('code.path')).toHaveText('/tmp/demo-bg1.output');
  await page.screenshot({ path: 'e2e/.results/background.png' });
  // stop the dev server
  await rows.filter({ hasText: 'npm run dev' }).getByRole('button', { name: 'Stop' }).click();
  await expect(rows.filter({ hasText: 'npm run dev' })).toContainText('stopped after');
  await expect(bar).not.toContainText('running');
  await expect(entry.locator('.bgbadge')).toHaveCount(0);
  // finished ones can be cleared (they also drop off by themselves after 10 minutes); then the bar goes
  await bar.getByRole('button', { name: 'Clear' }).click();
  await expect(bar).toHaveCount(0);
});
