import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { DAEMON_PORT, DATA } from '../playwright.config';
const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('any file attaches: dropped or picked, it goes to the machine and its path into the message', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill('/tmp');
  await dialog.getByLabel('Name in the tree').fill('Files');
  await dialog.getByRole('button', { name: 'Create session' }).click();

  const box = page.getByLabel('Message this session');
  await box.fill('read this');
  // picked with the paperclip
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello file') });
  await expect(box).toHaveValue(/^read this \/.*switchboard-uploads\/\w+\/notes\.txt $/);
  const path = (await box.inputValue()).split(' ')[2];
  expect(readFileSync(path, 'utf8')).toBe('hello file');
  // dropped on the chat
  const dt = await page.evaluateHandle(() => { const d = new DataTransfer(); d.items.add(new File(['%PDF'], 'spec sheet.pdf', { type: 'application/pdf' })); return d; });
  await page.getByRole('log', { name: 'Session history' }).dispatchEvent('drop', { dataTransfer: dt });
  await expect(box).toHaveValue(/"\/.*\/spec sheet\.pdf" $/);
});
