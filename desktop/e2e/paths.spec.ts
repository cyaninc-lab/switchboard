import { test, expect } from '@playwright/test';
import { looksLikePath } from '../src/lib/markdown';

// which inline code in Claude's replies becomes a clickable file link (no page needed)
test('file paths are recognised, other code is not', () => {
  for (const t of ['server/app.py', 'server/app.py:212', '/home/u/notes.md', '~/Projects/x', 'C:\\Tools\\rig\\server.js', 'README.md',
                   'package.json', 'src/lib/', './build.sh', 'docs\\HANDOVER.md'])
    expect(looksLikePath(t), t).toBe(true);
  for (const t of ['npm run check', 'foo()', 'x.map(f)', 'https://example.com/a.md', '3.14', 'v1.2', 'a < b', '...', 'GET /api/feed', 'true', 'dash_url'])
    expect(looksLikePath(t), t).toBe(false);
});

test('paths in step headers (Edit, Read) are clickable like those in replies', async ({ page, context }) => {
  const { execFileSync } = await import('node:child_process');
  const { DAEMON_PORT, DATA } = await import('../playwright.config');
  const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const { mkdtempSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const dir = mkdtempSync(join(tmpdir(), 'sb-e2e-'));
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(dir);
  await dialog.getByLabel('Name in the tree').fill('Step paths');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Step paths/ })).toBeVisible();
  const input = page.getByLabel('Message this session');
  await input.fill('touch up the notes');
  await input.press('Enter');
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(log.locator('.tool code.path', { hasText: 'notes.md' })).toBeVisible();  // Read(notes.md)
  const path = log.locator('.diffhead code.path', { hasText: 'notes.md' });  // Edit(notes.md)
  await expect(path).toBeVisible();
  // the edit's diff has line numbers, from Claude Code's result (its hunks), not just the before/after text
  const diff = log.locator('.diff', { has: page.locator('.diffhead', { hasText: 'notes.md' }) });
  await expect(diff.locator('.dl .ln')).toHaveText(['41', '42', '42', '43']);
  await expect(diff.locator('.dl.del')).toContainText('draft');
  await expect(diff.locator('.dl.add')).toContainText('final');
  // outside the desktop app (or for another machine's session) a click copies the full path
  await path.click();
  await expect(page.locator('.toast')).toContainText(`${dir}/notes.md`);
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`${dir}/notes.md`);
});

test('a claude.ai Artifact shows as a card that opens it', async ({ page }) => {
  const { execFileSync } = await import('node:child_process');
  const { DAEMON_PORT, DATA } = await import('../playwright.config');
  const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const { mkdtempSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-e2e-')));
  await dialog.getByLabel('Name in the tree').fill('Artifact card');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  const input = page.getByLabel('Message this session');
  await input.fill('make an artifact of the feed');
  await input.press('Enter');
  const cards = page.getByRole('log', { name: 'Session history' }).locator('.artcard');
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText('Feed report');
  await expect(cards.first()).toContainText('Published on claude.ai');
  await expect(cards.nth(1)).toContainText('Updated on claude.ai');  // Version 2
  await expect(cards.first()).toHaveAttribute('title', 'https://claude.ai/artifact/Demo123abc');
  const popup = page.waitForEvent('popup');  // outside the app: a new tab
  await cards.first().click();
  expect((await popup).url()).toContain('claude.ai/artifact/Demo123abc');
});
