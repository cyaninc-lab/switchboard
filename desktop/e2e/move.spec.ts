import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('drag a session onto another project (its header or a session in it) to move it there', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const projects: string[] = [];
  for (const name of ['Mover one', 'Mover two', 'Mover three']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    const dir = mkdtempSync(join(tmpdir(), 'sb-mv-'));
    projects.push(dir.split('/').pop()!);
    await dialog.getByLabel(/Folder on/).fill(dir);
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const entry = (n: string) => tree.locator('button.entry', { hasText: n });
  const header = (p: string) => tree.locator('button.group', { hasText: p });
  // the project a session sits under: the nearest project header above it
  const projectOf = (n: string) => entry(n).evaluate((el) => {
    let h: Element | null = el;
    while (h && !(h.matches('button.group'))) h = h.previousElementSibling;
    return h?.textContent?.replace(/^[▾▸]\s*/, '').split(' · ')[0].trim();
  });
  // onto a project header
  await entry('Mover one').dragTo(header(projects[1]));
  await expect.poll(() => projectOf('Mover one')).toBe(projects[1]);
  await expect(page.locator('.toast')).toContainText(`Moved Mover one to ${projects[1]}`);
  // onto a session in another project
  await entry('Mover three').dragTo(entry('Mover two'));
  await expect.poll(() => projectOf('Mover three')).toBe(projects[1]);
  // kept by the daemon
  await page.reload();
  await expect.poll(() => projectOf('Mover one')).toBe(projects[1]);
});
