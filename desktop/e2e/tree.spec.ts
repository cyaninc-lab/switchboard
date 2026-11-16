import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
const pairCode = () => execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim();

test('connect, add from a machine, read history, pin, remove', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('No machines yet')).toBeVisible();

  // connect with a one-time code (the code field is focused when the dialog opens)
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await expect(page.getByLabel('One-time code')).toBeFocused();
  await page.keyboard.type('WRONG123');
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByText('That code is wrong or expired')).toBeVisible();
  await page.getByLabel('One-time code').fill(pairCode());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');

  // add a session from the machine's list
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await expect(dialog.getByRole('button', { name: /Deploy #52 rolled back/ })).toBeVisible();
  await dialog.getByRole('button', { name: /Deploy #52 rolled back/ }).click();
  await dialog.getByLabel('Name in the tree').fill('Shorts deploy');
  await dialog.getByRole('button', { name: 'Add to tree' }).click();

  // it joins the tree under its project folder, opens, and shows the history terminal-style
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await expect(tree.getByText('shorts-app')).toBeVisible();
  await expect(tree.getByRole('button', { name: /Shorts deploy/ })).toBeVisible();
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(log).toContainText('Why did the deploy roll back?');
  await expect(log).toContainText('Bash(gh run view 4410');
  await expect(log).toContainText("KeyError: 'dash_url'");
  await expect(page.getByText('fix/feed-dash-url')).toBeVisible();
  await page.screenshot({ path: 'e2e/.results/history.png' });

  // a session already in the tree is marked and can't be added twice
  await page.getByRole('button', { name: '+ Add' }).click();
  await expect(dialog.getByRole('button', { name: /Deploy #52 rolled back/ })).toBeDisabled();
  await expect(dialog.getByText('in tree')).toBeVisible();
  // a session open in a terminal elsewhere shows as such once added
  await dialog.getByRole('button', { name: /Bump Glance/ }).click();
  await dialog.getByRole('button', { name: 'Add to tree' }).click();
  await expect(tree.getByRole('button', { name: /Bump Glance/ })).toHaveAttribute('title', /Open in a terminal/);

  // pin floats it to the top of its machine; remove takes it off the tree only
  await tree.getByRole('button', { name: /Shorts deploy/ }).click();
  await page.getByRole('button', { name: 'Pin', exact: true }).click();
  await expect(tree.getByRole('button', { name: /Shorts deploy/ })).toContainText('★');
  await page.getByRole('button', { name: 'Remove from tree' }).click();
  await expect(tree.getByRole('button', { name: /Shorts deploy/ })).toHaveCount(0);
  await page.getByRole('button', { name: '+ Add' }).click();
  await expect(dialog.getByRole('button', { name: /Deploy #52 rolled back/ })).toBeEnabled();
  await page.screenshot({ path: 'e2e/.results/add-dialog.png' });
});
