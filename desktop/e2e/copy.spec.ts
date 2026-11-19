import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('copy buttons: a code block copies its code, a reply copies its markdown', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-copy-')));
  await dialog.getByLabel('Name in the tree').fill('Copy things');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Copy things/ })).toBeVisible();
  const input = page.getByLabel('Message this session');
  await input.fill('share a snippet');
  await input.press('Enter');
  const log = page.getByRole('log', { name: 'Session history' });
  const reply = log.locator('.reply', { hasText: "That's the fix." });
  await expect(reply).toBeVisible();
  const clip = () => page.evaluate(() => navigator.clipboard.readText());

  const block = reply.locator('pre > button.copy');  // by its place: its name changes to "Copied"
  await expect(block).toHaveAccessibleName('Copy');
  await block.click();
  expect(await clip()).toBe("def feed(v):\n    return v.get('dash_url')");
  await expect(block).toHaveText('Copied');
  await expect(block).toHaveText('Copy', { timeout: 3000 });

  await reply.hover();
  await page.screenshot({ path: 'e2e/.results/copy.png' });
  await reply.getByRole('button', { name: 'Copy this reply' }).click();
  expect(await clip()).toBe("Here it is:\n\n```python\ndef feed(v):\n    return v.get('dash_url')\n```\n\nThat's the fix.");
});
