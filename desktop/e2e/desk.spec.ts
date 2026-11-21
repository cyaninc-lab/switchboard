import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
const url = `http://127.0.0.1:${DAEMON_PORT}`;

// two fresh sessions of their own (the specs share one demo daemon), and the page's notifications recorded
async function setup(page: import('@playwright/test').Page) {
  const pair = await page.request.post(`${url}/v1/pair`, { data: { code: execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim(), name: 'desk test' } });
  const token = (await pair.json()).token as string;
  const h = { Authorization: `Bearer ${token}` };
  const mk = async (name: string) => (await (await page.request.post(`${url}/v1/entries`, { headers: h, data: { new: { cwd: '/tmp' }, name } })).json()).id as string;
  const stamp = Date.now(), names = { a: `Builder ${stamp}`, b: `Other ${stamp}` };
  const [a, b] = [await mk(names.a), await mk(names.b)];
  await page.addInitScript(([u, t]) => {
    localStorage.setItem('switchboard.machines', JSON.stringify([{ url: u, token: t, name: 'demo' }]));
    (window as any).__notes = [];
    (window as any).Notification = class { static permission = 'granted'; static requestPermission = async () => 'granted';
      constructor(title: string, o: any) { (window as any).__notes.push({ title, body: o?.body ?? '' }); } };
  }, [url, token]);
  return { h, a, b, names };
}

test('desktop: a "finished" notification when a session you left finishes', async ({ page }) => {
  const { names } = await setup(page);
  await page.goto('/');
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await tree.getByRole('button', { name: names.a }).click();
  const input = page.getByLabel('Message this session');
  await input.fill('start the background build');
  await input.press('Enter');
  await expect(page.getByRole('log', { name: 'Session history' })).toContainText('Done: start the background build');
  await tree.getByRole('button', { name: names.b }).click();  // look elsewhere: Claude reports back on its own a second later
  await expect.poll(() => page.evaluate(() => (window as any).__notes), { timeout: 8000 })
    .toContainEqual(expect.objectContaining({ title: `${names.a} finished`, body: expect.stringContaining('Done: background task finished') }));
});

test('desktop: the daemons know you are in the window (phone notifications pause)', async ({ page }) => {
  const { h, names } = await setup(page);
  await page.goto('/');
  await page.bringToFront();
  await page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: names.a }).click();
  const atDesk = async () => (await (await page.request.get(`${url}/v1/push`, { headers: h })).json()).at_desk;
  await expect.poll(atDesk).toBe(true);
  await page.evaluate(() => { document.hasFocus = () => false; window.dispatchEvent(new Event('blur')); });  // switched to another window
  await expect.poll(atDesk).toBe(false);
});
