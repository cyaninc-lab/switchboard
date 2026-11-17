import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('subagents nest under their session, at any depth, and open read-only', async ({ page }) => {
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

  // collapsed: nothing is running (the session isn't live), so it shows a count
  const toggle = tree.getByRole('button', { name: /4 subagents/ });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  const group = tree.getByRole('group', { name: /Subagents of/ });
  const subs = group.locator('.entry.sub');
  await expect(subs).toHaveCount(4);
  // nesting: child below parent, indented one level more each time (3 levels deep)
  const pad = async (name: string) => parseInt(await group.getByRole('button', { name: new RegExp(name) }).evaluate((el) => getComputedStyle(el).paddingLeft));
  const [p1, p2, p3, top2] = [await pad('Investigate the failed deploy'), await pad('Read the feed handler'), await pad('Check which videos lack dash_url'), await pad('Summarise the logs')];
  expect(p2).toBeGreaterThan(p1);
  expect(p3).toBeGreaterThan(p2);
  expect(top2).toBe(p1);
  const order = await subs.allTextContents();
  expect(order.findIndex((t) => t.includes('Read the feed handler'))).toBe(order.findIndex((t) => t.includes('Investigate the failed deploy')) + 1);
  expect(order.findIndex((t) => t.includes('Check which videos'))).toBe(order.findIndex((t) => t.includes('Read the feed handler')) + 1);

  // clicking one shows what it did, read-only, with a way back
  await group.getByRole('button', { name: /Read the feed handler/ }).click();
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(page.locator('.termhead')).toContainText('subagent · Read the feed handler (Explore) · finished');
  await expect(log).toContainText('Grep');
  await expect(log).toContainText('server/app.py:212');
  await expect(log).toContainText('Agent: Check which videos lack dash_url'); // the subagent it spawned, as a link
  await expect(log).toContainText('Finished: Read the feed handler.');
  await expect(page.getByLabel('Message this session')).toHaveCount(0);
  await expect(page.getByText("Read-only: a subagent can't be messaged")).toBeVisible();
  await page.screenshot({ path: 'e2e/.results/subagents.png' });

  // the deepest one, through the link on the Agent call that spawned it
  await log.getByRole('button', { name: 'open subagent →' }).click();
  await expect(page.locator('.termhead')).toContainText('Check which videos lack dash_url');
  await expect(log).toContainText('Check which videos lack dash_url');

  // back to the session itself
  await page.locator('.termhead').getByRole('button', { name: /^←/ }).click();
  await expect(log).toContainText('Why did the deploy roll back?');
  await expect(page.getByLabel('Message this session')).toBeVisible();
  if (added) { // leave the shared demo daemon as we found it (later specs add this session themselves)
    await page.getByRole('button', { name: 'Remove from tree' }).click();
    await expect(parent).toHaveCount(0);
  }
});
