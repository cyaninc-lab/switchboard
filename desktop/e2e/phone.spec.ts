import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });  // a phone: narrow and touch

test('on a plain-http page (a phone on http://<machine>:7878: no randomUUID, no clipboard API) sending still clears the box', async ({ page }) => {
  await page.addInitScript(() => { delete (Crypto.prototype as any).randomUUID; Object.defineProperty(navigator, 'clipboard', { value: undefined }); });
  const errors: string[] = []; page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-http-')));
  await dialog.getByLabel('Name in the tree').fill('Plain http');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  const input = page.getByLabel('Message this session');
  await input.fill('hey what time is it');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(input).toHaveValue('');  // it stayed in the box when the id helper threw
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(log.locator('.you')).toContainText('hey what time is it');
  await expect(log).toContainText('Done: hey what time is it');
  await expect(page.locator('.error')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('phone layout: sessions → chat (as text messages) → back; inbox from the bottom bar; Send button', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-phone-')));
  await dialog.getByLabel('Name in the tree').fill('Phone chat');
  await dialog.getByRole('button', { name: 'Create session' }).click();

  // creating it opened its chat, full screen, with a back button and no session list
  const back = page.getByRole('button', { name: 'Back to sessions' });
  await expect(back).toBeVisible();
  await expect(tree).toBeHidden();
  const input = page.getByLabel('Message this session');
  // Enter makes a new line on a phone; the Send button sends
  await input.click();
  await input.press('Enter');
  await expect(input).toHaveValue('\n');
  await input.fill('touch up the notes please');
  await page.getByRole('button', { name: 'Send' }).click();
  const log = page.getByRole('log', { name: 'Session history' });
  await expect(log).toContainText('Done: touch up the notes please');
  // text messages: you on the right, Claude on the left, steps as cards on the left
  const lb = (await log.boundingBox())!;
  const mine = (await log.locator('.you').last().boundingBox())!, theirs = (await log.locator('.reply').last().boundingBox())!;
  expect(mine.x + mine.width).toBeGreaterThan(lb.x + lb.width - 40);
  expect(theirs.x).toBeLessThan(lb.x + 30);
  expect(mine.x).toBeGreaterThan(lb.x + 40);
  await page.screenshot({ path: 'e2e/.results/phone-chat.png' });

  // back to the list; the bottom bar switches to the inbox
  await back.click();
  await expect(tree).toBeVisible();
  await expect(tree.getByRole('button', { name: /Phone chat/ })).toBeVisible();
  await page.screenshot({ path: 'e2e/.results/phone-sessions.png' });
  const bar = page.getByRole('navigation', { name: 'Sections' });
  await bar.getByRole('button', { name: /Inbox/ }).click();
  await expect(page.getByRole('complementary', { name: 'Inbox' })).toBeVisible();
  await expect(tree).toBeHidden();
  await bar.getByRole('button', { name: 'Sessions' }).click();
  await expect(tree).toBeVisible();

});

test.describe('a narrow desktop window', () => {
  test.use({ viewport: { width: 520, height: 800 }, hasTouch: false, isMobile: false });
  test('keeps the desktop layout (no bottom bar; Enter sends)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('navigation', { name: 'Sections' })).toHaveCount(0);
  });
});
