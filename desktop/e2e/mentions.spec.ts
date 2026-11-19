import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('@ in the message box lists the session folder\'s files; picking one inserts @path', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const dir = mkdtempSync(join(tmpdir(), 'sb-at-'));
  mkdirSync(join(dir, 'src'));
  for (const f of ['README.md', 'src/readme_helper.py', 'src/app.py', 'my notes.md']) writeFileSync(join(dir, f), 'x');
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(dir);
  await dialog.getByLabel('Name in the tree').fill('Mentions');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Mentions/ })).toBeVisible();

  const input = page.getByLabel('Message this session');
  const files = page.getByRole('listbox', { name: 'Files' });
  await input.pressSequentially('look at @rea');
  await expect(files.getByRole('option')).toHaveText([/README\.md/, /src\/readme_helper\.py/]);  // name starts with, first
  await page.screenshot({ path: 'e2e/.results/mentions.png' });
  await input.press('ArrowDown');
  await input.press('Enter');  // picks, doesn't send
  await expect(input).toHaveValue('look at @src/readme_helper.py ');
  await expect(files).toHaveCount(0);
  // a click works too; a name with a space is quoted
  await input.pressSequentially('and @notes');
  await files.getByRole('option', { name: /my notes\.md/ }).click();
  await expect(input).toHaveValue('look at @src/readme_helper.py and @"my notes.md" ');
  // Esc closes the list without touching the text; an email-like word doesn't open it
  await input.pressSequentially('@ap');
  await expect(files).toBeVisible();
  await input.press('Escape');
  await expect(files).toHaveCount(0);
  await expect(input).toHaveValue('look at @src/readme_helper.py and @"my notes.md" @ap');
  await input.fill('mail me@app');
  await input.press('End');
  await page.waitForTimeout(400);
  await expect(files).toHaveCount(0);
});
