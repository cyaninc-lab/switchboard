import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('a machine folds like a project, and stays folded', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill('/tmp');
  await dialog.getByLabel('Name in the tree').fill('Fold me');
  await dialog.getByRole('button', { name: 'Create session' }).click();

  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const fold = tree.getByRole('button', { name: 'Fold demo' });
  await expect(tree.getByRole('button', { name: /Fold me/ })).toBeVisible();
  await fold.click();
  await expect(tree.getByRole('button', { name: /Fold me/ })).toHaveCount(0);  // its sessions are hidden
  const unfold = tree.getByRole('button', { name: 'Unfold demo' });
  await expect(unfold).toHaveAttribute('aria-expanded', 'false');
  await expect(unfold).toContainText(/· \d+/);  // how many sessions it holds
  await page.reload();
  await expect(tree.getByRole('button', { name: 'Unfold demo' })).toBeVisible();  // remembered on this device
  await tree.getByRole('button', { name: 'Unfold demo' }).click();
  await expect(tree.getByRole('button', { name: /Fold me/ })).toBeVisible();
});
