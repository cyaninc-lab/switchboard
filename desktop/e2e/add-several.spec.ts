import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('add several sessions at once', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  const rows = dialog.getByRole('group', { name: 'Sessions' }).getByRole('button');
  await expect(rows.first()).toBeVisible();
  // two canned sessions not in the tree yet (the specs share one demo daemon)
  const free: string[] = [];
  for (const r of await rows.all()) if (!(await r.isDisabled())) free.push((await r.locator('.stitle').textContent())!.trim());
  expect(free.length).toBeGreaterThanOrEqual(2);
  const [one, two] = free;
  await dialog.getByRole('button', { name: new RegExp(one) }).click();
  await expect(dialog.getByLabel('Name in the tree')).toHaveValue(one);  // one picked: name it
  await dialog.getByRole('button', { name: new RegExp(two) }).click();
  await expect(dialog.getByLabel('Name in the tree')).toHaveCount(0);  // several: each keeps its title
  await expect(dialog).toContainText('2 sessions, each named after its title');
  await dialog.getByLabel('Group').fill('Batch');
  await dialog.getByRole('button', { name: 'Add 2 to tree' }).click();

  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await expect(tree.getByRole('button', { name: /Batch/ })).toBeVisible();
  for (const name of [one, two]) await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();

  // tidy up
  for (const name of [one, two]) {
    await tree.getByRole('button', { name: new RegExp(name) }).click();
    await page.getByRole('button', { name: 'Remove from tree' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toHaveCount(0);
  }
});
