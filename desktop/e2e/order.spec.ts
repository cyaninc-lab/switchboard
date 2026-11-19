import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('within a project, the most recently active session is on top; the order holds still under the pointer', async ({ page }) => {
  const url = `http://127.0.0.1:${DAEMON_PORT}`;
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(url);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const dir = mkdtempSync(join(tmpdir(), 'sb-e2e-'));  // one folder: both land in the same project
  for (const name of ['Order X', 'Order Y']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(dir);
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  const order = async () => (await tree.locator('.entry:not(.sub) .ename').allTextContents()).filter((t) => t.startsWith('Order '));
  const input = page.getByLabel('Message this session');
  const send = async (who: string, text: string) => { await tree.getByRole('button', { name: new RegExp(who) }).click(); await input.fill(text); await input.press('Enter'); await page.mouse.move(700, 400); };

  await send('Order X', 'hello from x');
  await expect.poll(order).toEqual(['Order X', 'Order Y']);
  await send('Order Y', 'hello from y');
  await expect.poll(order).toEqual(['Order Y', 'Order X']);

  // pointer over the tree: X gets busy (sent from elsewhere), but nothing moves until the pointer leaves
  await tree.getByRole('button', { name: /Order Y/ }).hover();
  await expect.poll(() => page.evaluate(() => document.querySelector('nav.tree')?.matches(':hover'))).toBe(true);
  await page.evaluate(async ({ url }) => {
    const m = JSON.parse(localStorage.getItem('switchboard.machines')!).find((x: any) => x.url === url);
    const r = await (await fetch(`${url}/v1/entries`, { headers: { Authorization: `Bearer ${m.token}` } })).json();
    const x = r.entries.find((e: any) => e.name === 'Order X');
    const ws = new WebSocket(url.replace('http', 'ws') + '/v1/ws');
    await new Promise((ok) => (ws.onopen = ok));
    ws.send(JSON.stringify({ type: 'auth', token: m.token }));
    ws.send(JSON.stringify({ type: 'send', entry: x.id, text: 'busy from elsewhere' }));
    await new Promise((ok) => setTimeout(ok, 1500));
    ws.close();
  }, { url });
  expect(await order()).toEqual(['Order Y', 'Order X']);  // held
  await page.mouse.move(700, 400);  // away from the tree
  await expect.poll(order).toEqual(['Order X', 'Order Y']);
});
