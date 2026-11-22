import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

// The connection died without closing (a network blip): what you send vanishes, then the socket closes. The message
// was never acknowledged, so it goes again after reconnecting, and arrives once.
test('a message sent into a dead connection arrives after the reconnect, once', async ({ page }) => {
  await page.addInitScript(() => {
    const W = window.WebSocket, all: WebSocket[] = [];
    (window as any).__sockets = all; (window as any).__swallow = false;
    (window as any).WebSocket = class extends W {
      constructor(u: string | URL, p?: string | string[]) { super(u, p); all.push(this); }
      send(d: any) { if (!(window as any).__swallow) super.send(d); }  // a dead connection: sending "works", nothing arrives
    };
  });
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill('/tmp');
  await dialog.getByLabel('Name in the tree').fill('Resend');
  await dialog.getByRole('button', { name: 'Create session' }).click();

  const input = page.getByLabel('Message this session');
  const log = page.getByRole('log', { name: 'Session history' });
  await page.evaluate(() => { (window as any).__swallow = true; });
  await input.fill('did this arrive?');
  await input.press('Enter');
  await page.waitForTimeout(500);
  await expect(log).not.toContainText('Done: did this arrive?');  // lost in the dead connection
  await page.evaluate(() => { (window as any).__swallow = false; for (const w of (window as any).__sockets) w.close(); });  // it closes at last
  await expect(log).toContainText('Done: did this arrive?', { timeout: 10_000 });  // sent again after reconnecting
  await page.waitForTimeout(1000);
  expect((await log.textContent())!.split('Done: did this arrive?').length - 1).toBe(1);  // and only once
});
