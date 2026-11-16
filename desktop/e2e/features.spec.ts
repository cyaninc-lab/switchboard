import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==', 'base64');

test('images and slash commands', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('button', { name: /Untitled session/ }).click();
  await dialog.getByRole('button', { name: 'Add to tree' }).click();
  const log = page.getByRole('log', { name: 'Session history' });
  const input = page.getByLabel('Message this session');

  // image: attach, see the thumbnail, send, Claude sees it, it shows in the history
  await page.locator('input[type=file]').setInputFiles({ name: 'dot.png', mimeType: 'image/png', buffer: PNG });
  await expect(page.getByLabel('Attached images').getByRole('img', { name: 'dot.png' })).toBeVisible();
  await input.fill('what is this?');
  await input.press('Enter');
  await expect(log).toContainText('I can see 1 image(s).');
  await expect(log.getByRole('img', { name: 'Attachment' })).toBeVisible();
  await expect(page.getByLabel('Attached images')).toHaveCount(0);
  await page.screenshot({ path: 'e2e/.results/image.png' });

  // slash commands: the menu comes from the session's own list (sent at session start)
  await input.fill('/');
  const menu = page.getByRole('listbox', { name: 'Slash commands' });
  await expect(menu.getByRole('option', { name: /\/compact/ })).toBeVisible();
  await input.fill('/us');
  await expect(menu.getByRole('option')).toHaveCount(1);
  await expect(menu).toContainText('Show plan usage and limits');
  await page.screenshot({ path: 'e2e/.results/slash.png' });
  await input.press('Tab');
  await expect(input).toHaveValue('/usage ');
  await input.press('Enter');
  await expect(log).toContainText('Plan usage: 12%');
  await expect(log.locator('.md strong', { hasText: 'Plan usage:' })).toBeVisible();  // markdown rendered, not **raw**

  // a small window keeps following new output
  await page.setViewportSize({ width: 1000, height: 420 });
  for (const n of [1, 2, 3]) { await input.fill(`message ${n}`); await input.press('Enter'); await expect(log).toContainText(`Done: message ${n}`); }
  const atBottom = await log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 4);
  expect(atBottom).toBe(true);
  await page.setViewportSize({ width: 1280, height: 820 });

  // a non-image file is refused with a message
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hi') });
  await expect(page.getByRole('alert')).toContainText("isn't a PNG, JPEG, GIF or WebP");
});
