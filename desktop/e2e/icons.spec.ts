import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('tree icons: ring when live, spinner when working, sun when waiting, dot when unread', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  const tree = page.getByRole('navigation', { name: 'Sessions' });
  // fresh sessions: the other specs share this daemon and need the canned ones
  const folders: string[] = [];
  for (const name of ['Icon A', 'Icon B']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    folders.push(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
    await dialog.getByLabel(/Folder on/).fill(folders.at(-1)!);
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const a = tree.getByRole('button', { name: /^Icon A(?! renamed)/ }), b = tree.getByRole('button', { name: /Icon B/ });
  const st = (e: typeof a) => e.locator('.st');
  const input = page.getByLabel('Message this session');

  // B: waiting on a permission -> sun; allowed -> ring
  await input.fill('please run the checks');
  await input.press('Enter');
  await expect(st(b)).toHaveClass(/waiting/);
  // a folded group hides its sessions but still shows that one needs you
  const groupB = tree.getByRole('button', { name: new RegExp(folders[1].split('/').pop()!) });
  await groupB.click();
  await expect(groupB).toHaveAttribute('aria-expanded', 'false');
  await expect(b).toHaveCount(0);
  await expect(groupB.locator('.st.waiting')).toBeVisible();
  await groupB.click();
  await expect(b).toBeVisible();
  await page.getByRole('group', { name: 'Permission needed' }).getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(st(b)).toHaveClass(/ready/);

  // B: working -> spinner
  await input.fill('a slow job');
  await input.press('Enter');
  await expect(st(b)).toHaveClass(/working/);
  await page.keyboard.press('Escape');
  await expect(st(b)).toHaveClass(/ready/, { timeout: 5000 });

  // B replies while A is open -> dot on B; opening B clears it
  await page.getByRole('button', { name: 'Model, effort, mode and account' }).click(); await page.getByRole('dialog', { name: 'Session settings' }).getByLabel('Permission mode').selectOption('auto'); await page.getByRole('dialog', { name: 'Session settings' }).getByRole('button', { name: 'Close' }).click();
  await input.fill('start a background job');
  await input.press('Enter');
  await expect(st(b)).toHaveClass(/ready/);
  await a.click();
  await expect(st(b)).toHaveClass(/unread/, { timeout: 5000 });
  await expect(b).toHaveAttribute('title', 'New message');
  await b.click();
  await expect(st(b)).toHaveClass(/ready/);

  // an unsent draft (text and images) stays with its session
  const pics = page.getByLabel('Attached images');
  await input.fill('half-written for B');
  await page.locator('input[type=file]').setInputFiles({ name: 'dot.png', mimeType: 'image/png', buffer: PNG });
  await expect(pics.getByRole('img', { name: 'dot.png' })).toBeVisible();
  await a.click();
  await expect(input).toHaveValue('');
  await expect(pics).toHaveCount(0);
  await input.fill('for A');
  await b.click();
  await expect(input).toHaveValue('half-written for B');
  await expect(pics.getByRole('img', { name: 'dot.png' })).toBeVisible();
  await a.click();
  await expect(input).toHaveValue('for A');

  // a half-answered question survives switching away and back
  await b.click();
  await input.fill('ask me something');
  await input.press('Enter');
  const q = page.getByRole('group', { name: 'Claude asks' });
  await q.getByLabel(/Other answer to/).fill('half an answer');
  await a.click();
  await expect(q).toHaveCount(0);
  await b.click();
  await expect(q.getByLabel(/Other answer to/)).toHaveValue('half an answer');
  await q.getByLabel(/Other answer to/).press('Enter');
  await expect(q).toHaveCount(0);
  await expect(st(b)).toHaveClass(/ready/);

  // right-click > Rename: edit in place, Enter saves, Escape cancels; a project renames for all its sessions
  await a.click({ button: 'right' });
  await page.getByRole('menu', { name: 'Icon A actions' }).getByRole('menuitem', { name: 'Rename' }).click();
  const box = tree.getByLabel('Rename Icon A');
  await expect(box).toBeFocused();
  await box.fill('Icon A renamed');
  await box.press('Enter');
  const a2 = tree.getByRole('button', { name: /Icon A renamed/ });
  await expect(a2).toBeVisible();
  await a2.click({ button: 'right' });
  await page.getByRole('menu', { name: 'Icon A renamed actions' }).getByRole('menuitem', { name: 'Rename' }).click();
  await tree.getByLabel('Rename Icon A renamed').fill('nope');
  await tree.getByLabel('Rename Icon A renamed').press('Escape');
  await expect(a2).toBeVisible();
  const groupA = folders[0].split('/').pop()!;
  await tree.getByRole('button', { name: new RegExp(groupA) }).click({ button: 'right' });
  await page.getByRole('menu', { name: `${groupA} project actions` }).getByRole('menuitem', { name: 'Rename project' }).click();
  await tree.getByLabel(`Rename project ${groupA}`).fill('Renamed project');
  await tree.getByLabel(`Rename project ${groupA}`).press('Enter');
  await expect(tree.getByRole('button', { name: /Renamed project/ })).toBeVisible();
  await expect(tree.getByRole('button', { name: new RegExp(groupA) })).toHaveCount(0);
  await expect(a2).toBeVisible();

  // right-click > Close session ends the live session; it stays in the tree
  const menu = page.getByRole('menu', { name: 'Icon B actions' });
  await b.click({ button: 'right' });
  await expect(menu).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await b.click({ button: 'right' });
  await menu.getByRole('menuitem', { name: 'Close session' }).click();
  await expect(menu).toHaveCount(0);
  await expect(st(b)).toHaveClass(/stopped/);
  await expect(b).toHaveAttribute('title', /Stopped/);
  await b.click({ button: 'right' });
  await expect(menu.getByRole('menuitem', { name: 'Close session' })).toBeDisabled();  // nothing to close
});
