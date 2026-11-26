import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('only running subagents show in the tree; a subagent can be messaged through its session', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('.chip', { hasText: 'demo' })).toContainText('online');

  // the demo session with subagents (another spec may already have added it)
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  const pick = dialog.getByRole('button', { name: /Deploy #52 rolled back/ });
  await expect(pick).toBeVisible();
  const added = !(await pick.isDisabled());
  if (!added) await dialog.getByRole('button', { name: 'Cancel' }).click();
  else { await pick.click(); await dialog.getByRole('button', { name: 'Add to tree' }).click(); }
  const parent = tree.locator('.entry:not(.sub)').filter({ hasText: /Deploy #52|Shorts deploy/ }).first();
  await expect(parent).toBeVisible();

  // the session isn't live, so none of its subagents is running: nothing listed (finished/stopped ones never are)
  await expect(tree.locator('.subtoggle')).toHaveCount(0);
  await parent.click();
  await page.getByLabel('Message this session').fill('hello');
  await page.getByLabel('Message this session').press('Enter');

  // live now: the 2 unfinished ones show, nested (a2's finished parent a1 is hidden, so a2 is at the top)
  const toggle = tree.getByRole('button', { name: '2 subagents running' });
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const group = tree.getByRole('group', { name: /Subagents of/ });
  const subs = group.locator('.entry.sub');
  await expect(subs).toHaveCount(2);
  await expect(group).not.toContainText('Investigate the failed deploy');
  await expect(group).not.toContainText('Summarise the logs');
  const pad = async (name: string) => parseInt(await group.getByRole('button', { name: new RegExp(name) }).evaluate((el) => getComputedStyle(el).paddingLeft));
  expect(await pad('Check which videos lack dash_url')).toBeGreaterThan(await pad('Read the feed handler'));

  // open one: what it did, and a box that messages it through the parent session
  await group.getByRole('button', { name: /Read the feed handler/ }).click();
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(page.locator('.termhead')).toContainText('subagent · Read the feed handler (Explore) · running');
  await expect(log).toContainText('server/app.py:212');
  await expect(page.getByLabel('Message this session')).toHaveCount(0);
  await expect(page.getByText('passes it on with SendMessage')).toBeVisible();
  await page.getByLabel('Message this subagent').fill('What did you find?');
  await page.getByLabel('Message this subagent').press('Enter');
  await expect(page.getByLabel('Message this subagent')).toHaveValue('');
  await page.screenshot({ path: 'e2e/.results/subagents.png' });

  // the parent got the relay request, naming the subagent
  await page.locator('.termhead').getByRole('button', { name: /^←/ }).click();
  await expect(log).toContainText('Use the SendMessage tool to send this message to agent a2');
  await expect(log).toContainText('What did you find?');

  await expect(page.getByLabel('Message this session')).toBeVisible();
  if (added) { // leave the shared demo daemon as we found it (later specs add this session themselves)
    await page.locator('nav .entry.sel').click({ button: 'right' }); await page.getByRole('menuitem', { name: 'Remove from tree' }).click();
    await expect(parent).toHaveCount(0);
  }
});
