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
  // while Claude works the box's hint and the interrupt button stay small enough for a phone
  await input.fill('slow one please');
  await page.getByRole('button', { name: 'Send' }).click();
  // what streams in is already the left bubble Claude's reply will be (no jump when it finishes)
  const live = log.locator('.reply.streaming');
  await expect(live).toBeVisible();
  const lb2 = (await log.boundingBox())!, sb = (await live.boundingBox())!;
  expect(sb.x).toBeLessThan(lb2.x + 30);
  expect(await live.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  await expect(input).toHaveAttribute('placeholder', 'Working… Claude reads it after this step');
  const stop = page.getByRole('button', { name: 'Interrupt' });
  await expect(stop).toHaveText('■');
  const box = (await page.locator('form.inputbar').boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(390);
  await stop.click();
  await expect(input).toHaveAttribute('placeholder', 'Message');
  // text messages: you on the right, Claude on the left, steps as cards on the left
  const lb = (await log.boundingBox())!;
  const mine = (await log.locator('.you').last().boundingBox())!, theirs = (await log.locator('.reply').last().boundingBox())!;
  expect(mine.x + mine.width).toBeGreaterThan(lb.x + lb.width - 40);
  expect(theirs.x).toBeLessThan(lb.x + 30);
  expect(mine.x).toBeGreaterThan(lb.x + 40);
  await page.screenshot({ path: 'e2e/.results/phone-chat.png' });

  // a side chat takes the whole screen; ‹ goes back to the chat (it stays open), /btw in the header returns to it
  await input.fill('/btw what changed?');
  await page.getByRole('button', { name: 'Send' }).click();
  const side = page.getByRole('complementary', { name: 'Side chat' });
  await expect(side).toContainText('Side answer: what changed?');
  expect((await side.boundingBox())!.width).toBeGreaterThan(380);
  await expect(log).toBeHidden();
  await side.getByRole('button', { name: 'Back to the chat' }).click();
  await expect(log).toBeVisible();
  await expect(side).toHaveCount(0);
  // the message box is its normal height again (squeezed narrow, it used to stay tall)
  expect((await input.boundingBox())!.height).toBeLessThan(80);
  await page.getByRole('button', { name: '/btw' }).click();
  await expect(side).toContainText('Side answer: what changed?');
  await side.getByRole('button', { name: 'Back to the chat' }).click();

  // a tall phone screenshot keeps its shape (it was squashed wide); it sits on your side
  const { deflateSync } = await import('node:zlib');
  const png = (w: number, h: number) => {  // a plain w×h PNG, no image library needed
    const crcT = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
    const crc = (b: Buffer) => { let c = 0xffffffff; for (const x of b) c = crcT[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
    const chunk = (t: string, d: Buffer) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
    const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
    const raw = Buffer.alloc((w * 3 + 1) * h, 0x80); for (let y = 0; y < h; y++) raw[y * (w * 3 + 1)] = 0;
    return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
  };
  await page.locator('input[type=file]').setInputFiles({ name: 'screenshot.png', mimeType: 'image/png', buffer: png(90, 200) });
  await input.fill('look at this');
  await page.getByRole('button', { name: 'Send' }).click();
  const shot = log.locator('img.shot').last();
  await expect(shot).toBeVisible();
  const ib = (await shot.boundingBox())!;
  expect(ib.height / ib.width).toBeGreaterThan(2.1);  // 200/90: still tall
  expect(ib.height / ib.width).toBeLessThan(2.35);
  expect(ib.x + ib.width).toBeGreaterThan(lb.x + lb.width - 40);  // on the right
  // attach and Send are the same size of square
  const clip = (await page.getByRole('button', { name: 'Attach images' }).boundingBox())!, send = (await page.getByRole('button', { name: 'Send' }).boundingBox())!;
  expect(Math.abs(clip.width - clip.height)).toBeLessThan(2);
  expect(Math.abs(clip.width - send.width)).toBeLessThan(2);

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

test('on a phone, ⋯ → Session settings changes the mode (the desktop keeps these in its status bar, hidden on a phone)', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-opts-')));
  await dialog.getByLabel('Name in the tree').fill('Phone opts');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await expect(page.getByLabel('Message this session')).toBeVisible();
  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Session settings…' }).click();
  const sheet = page.getByRole('dialog', { name: 'Session settings' });
  await expect(sheet.getByLabel('Model')).toBeVisible();
  await sheet.getByLabel('Permission mode').selectOption('plan');
  await sheet.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: 'More' }).click();  // reopened: it kept the change
  await page.getByRole('menuitem', { name: 'Session settings…' }).click();
  await expect(page.getByRole('dialog', { name: 'Session settings' }).getByLabel('Permission mode')).toHaveValue('plan');
});
