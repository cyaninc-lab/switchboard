import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('chat: stream, approve, deny, interrupt, auto mode, stop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();

  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('button', { name: /Investigate slow feed/ }).click();
  await dialog.getByRole('button', { name: 'Add to tree' }).click();

  const log = page.getByRole('log', { name: 'Session history' });
  const input = page.getByLabel('Message this session');
  const entry = page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: /Investigate slow feed/ });

  // Shift+Enter starts a new line, the box grows; Enter sends both lines and it shrinks back
  const h0 = (await input.boundingBox())!.height;
  await input.click();
  await page.keyboard.type('first line');
  await page.keyboard.press('Shift+Enter');
  await page.keyboard.type('second line');
  await expect(input).toHaveValue('first line\nsecond line');
  expect((await input.boundingBox())!.height).toBeGreaterThan(h0);
  await page.keyboard.press('Enter');
  await expect(log).toContainText('first line\nsecond line');
  await expect(input).toHaveValue('');
  await expect.poll(async () => (await input.boundingBox())!.height).toBe(h0);
  await expect(entry).toHaveAttribute('title', 'Live and ready');

  // approve
  await input.fill('please run the checks');
  await input.press('Enter');
  await expect(log).toContainText('please run the checks');
  const card = page.getByRole('group', { name: 'Permission needed' });
  await expect(card).toContainText('Bash: git status');
  await expect(entry).toHaveAttribute('title', 'Waiting for you');
  await page.screenshot({ path: 'e2e/.results/permission.png' });
  await card.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(log).toContainText('nothing to commit');
  await expect(log).toContainText('Done: please run the checks');
  await expect(entry).toHaveAttribute('title', 'Live and ready');

  // deny
  await input.fill('run it again');
  await input.press('Enter');
  await card.getByRole('button', { name: 'Deny' }).click();
  await expect(log).toContainText('Permission denied');

  // interrupt a slow task with Esc
  await expect(entry).toHaveAttribute('title', 'Live and ready');
  await input.fill('a slow job');
  await input.press('Enter');
  await expect(page.getByRole('button', { name: 'Stop Claude' })).toHaveText('■');  // Send turns into Stop while it works
  await expect(page.getByRole('status', { name: 'Claude is working' })).toBeVisible();  // the ❯ by the box spins
  await page.keyboard.press('Escape');
  await expect(entry).toHaveAttribute('title', 'Live and ready', { timeout: 5000 });

  // Ctrl+Enter while working: interrupt and send (the slow job would take 30 s)
  await input.fill('a slow job');
  await input.press('Enter');
  await expect(entry).toHaveAttribute('title', 'Working');
  await input.fill('jump the queue');
  await input.press('Control+Enter');
  await expect(log).toContainText('Done: jump the queue', { timeout: 8000 });
  await expect(entry).toHaveAttribute('title', 'Live and ready');

  // sent while Claude is busy: it goes in at once (Claude reads it after its current step), so it joins the chat now
  const pending = page.getByRole('list', { name: 'Queued messages' });
  await input.fill('please run the checks');
  await input.press('Enter');
  await expect(card).toBeVisible();
  await input.fill('after the checks');
  await input.press('Enter');
  await expect(log).toContainText('after the checks');
  await expect(pending).toHaveCount(0);
  await card.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(log).toContainText('Done: after the checks');
  const text = (await log.textContent())!;
  expect(text.lastIndexOf('❯after the checks')).toBeLessThan(text.lastIndexOf('Done: after the checks'));
  await expect(entry).toHaveAttribute('title', 'Live and ready');
  await input.fill('');

  // auto mode: no prompt
  await page.getByRole('button', { name: 'Model, effort, mode and account' }).click(); await page.getByRole('dialog', { name: 'Session settings' }).getByLabel('Permission mode').selectOption('auto'); await page.getByRole('dialog', { name: 'Session settings' }).getByRole('button', { name: 'Close' }).click();
  await input.fill('run in auto');
  await input.press('Enter');
  await expect(log).toContainText('Done: run in auto');
  await expect(card).toHaveCount(0);

  // Stop interrupts what it's doing; the session stays open (no re-reading its history on the next message)
  await input.fill('a slow job');
  await input.press('Enter');
  await expect(entry).toHaveAttribute('title', 'Working');
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(entry).toHaveAttribute('title', 'Live and ready', { timeout: 5000 });
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);  // nothing to stop now

  // ⋯ → Close session ends the process; it stays in the tree
  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Close session' }).click();
  await expect(entry).toHaveAttribute('title', /Stopped/);
  await page.screenshot({ path: 'e2e/.results/chat.png' });
});
