import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('drag a project to reorder; the machine keeps the order, so it survives a restart', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const projects: string[] = [];
  for (const name of ['Proj one', 'Proj two', 'Proj three']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    const dir = mkdtempSync(join(tmpdir(), 'sb-proj-'));
    projects.push(dir.split('/').pop()!);
    await dialog.getByLabel(/Folder on/).fill(dir);
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const mine = async () => (await tree.locator('button.group').allTextContents()).map((t) => t.replace(/^[▾▸]\s*/, '').split(' · ')[0].trim()).filter((t) => projects.includes(t));
  const header = (p: string) => tree.locator('button.group', { hasText: p });
  const before = await mine();
  expect(before).toHaveLength(3);
  // drag the last of the three above the first
  await header(before[2]).dragTo(header(before[0]));
  const want = [before[2], before[0], before[1]];
  await expect.poll(mine).toEqual(want);
  await page.reload();
  await expect.poll(mine).toEqual(want);  // kept by the daemon, not this window
});
