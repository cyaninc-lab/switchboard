// README screenshots (demo data only), into docs/images: README_SHOTS=1 npx playwright test e2e/readme-shots.spec.ts
// (skipped in normal test runs)
import { test, expect, devices } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';
const OUT = '../docs/images/';
test.skip(!process.env.README_SHOTS, 'README screenshots: only when asked for');

async function connect(page: any) {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
}
async function create(page: any, name: string, folder: string) {
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill(folder);
  await dialog.getByLabel('Name in the tree').fill(name);
  await dialog.getByRole('button', { name: 'Create session' }).click();
  await page.getByRole('tablist', { name: 'Open sessions' }).getByRole('tab', { name }).and(page.locator('[aria-selected="true"]')).waitFor();
}
const say = async (page: any, text: string) => { const b = page.getByLabel('Message this session'); await b.fill(text); await b.press('Enter'); };

test('readme: desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 880 });
  await connect(page);
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  for (const n of [/Deploy #52/, /Bump Glance/, /Investigate slow feed/]) await dialog.getByRole('button', { name: n }).click();
  await dialog.getByRole('button', { name: /Add 3 to tree/ }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  const base = mkdtempSync(join(tmpdir(), 'sb-readme-'));
  for (const [n, f] of [['Docs rewrite', 'docs'], ['Nightly report', 'reports'], ['API tests', 'api']] as const) {
    execFileSync('mkdir', ['-p', join(base, f)]);
    await create(page, n, join(base, f));
  }
  await page.waitForTimeout(500);
  await say(page, 'run the checks'); // waits on you: Allow / Deny
  await page.getByRole('group', { name: 'Permission needed' }).waitFor();
  await tree.getByRole('button', { name: /Docs rewrite/ }).click();
  await page.getByRole('tablist', { name: 'Open sessions' }).getByRole('tab', { name: 'Docs rewrite' }).and(page.locator('[aria-selected="true"]')).waitFor();
  await say(page, 'ask me');
  await page.getByRole('radiogroup').first().waitFor();
  // the inbox: what watches found
  const tok = await (await page.request.post(`http://127.0.0.1:${DAEMON_PORT}/v1/pair`, { data: { code: execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim(), client_name: 'readme' } })).json();
  const h = { Authorization: `Bearer ${tok.token}` };
  for (const [name, cmd] of [['Nightly backup', 'echo "backup finished: 14.2 GB, 0 errors"'], ['Disk space', 'echo "/mnt/storage at 91% (was 84% last week)"']]) {
    const w = await (await page.request.post(`http://127.0.0.1:${DAEMON_PORT}/v1/watches`, { headers: h, data: { name, kind: 'command', config: { command: cmd, every_minutes: 1440 }, action: 'notify' } })).json();
    await page.request.post(`http://127.0.0.1:${DAEMON_PORT}/v1/watches/${w.id}/test`, { headers: h });
  }
  await tree.getByRole('button', { name: /Deploy #52/ }).click();
  await expect(page.getByRole('log', { name: 'Session history' })).toContainText('rollback');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: OUT + 'desktop.png' });
  // a question
  await tree.getByRole('button', { name: /Docs rewrite/ }).click();
  await page.waitForTimeout(600);
  await page.locator('.card.q').screenshot({ path: OUT + 'question.png' });
});

test('readme: split view', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 880 });
  await connect(page);
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await tree.getByRole('button', { name: /Deploy #52/ }).click();
  await tree.getByRole('button', { name: /API tests/ }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Open to the side' }).click();
  await tree.getByRole('button', { name: /Investigate slow feed/ }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Open to the side' }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: OUT + 'split.png' });
});

test('readme: phone', async ({ browser }) => {
  const ctx = await browser.newContext({ ...devices['Pixel 7'], viewport: { width: 412, height: 760 } });
  const page = await ctx.newPage();
  await connect(page);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: OUT + 'phone-sessions.png' });
  await page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: /Deploy #52/ }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: OUT + 'phone-chat.png' });
  await ctx.close();
});
