import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('"Ask Claude to check something": a quiet wake watch with work hours, from a template', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  await page.getByRole('button', { name: '+ Add' }).click();
  const add = page.getByRole('form', { name: 'Add to Switchboard' });
  await add.getByRole('tab', { name: 'New session' }).click();
  await add.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-wt-')));
  await add.getByLabel('Name in the tree').fill('Zoho mentions');
  await add.getByRole('button', { name: 'Create session' }).click();
  await expect(tree.getByRole('button', { name: /Zoho mentions/ })).toBeVisible();

  await page.getByRole('button', { name: 'Watches' }).click();
  await page.getByRole('button', { name: 'Ask Claude to check something…' }).click();
  const form = page.getByRole('form', { name: 'Edit watch' });
  await expect(form.getByRole('radio', { name: 'Wake an agent' })).toHaveAttribute('aria-checked', 'true');
  await expect(form.getByLabel(/Only tell me when there's something new/)).toBeChecked();
  await expect(form.getByLabel('Only at certain times')).toBeChecked();
  await expect(form.getByLabel('From')).toHaveValue('08:30');
  await expect(form.getByRole('group', { name: 'Days' }).getByRole('button', { name: 'Sat' })).toHaveAttribute('aria-pressed', 'false');
  await form.getByLabel('Name', { exact: true }).fill('Zoho mentions');
  await form.getByLabel('Session to wake').selectOption({ label: 'Zoho mentions' });
  await form.getByLabel('to', { exact: true }).fill('17:00');
  await form.getByRole('group', { name: 'Days' }).getByRole('button', { name: 'Fri' }).click();  // not on Fridays
  await page.screenshot({ path: 'e2e/.results/watch-template.png' });
  await form.getByRole('button', { name: 'Save watch' }).click();
  await expect(page.getByText(/scheduled · every 15 min · 08:30–17:00 Mon Tue Wed Thu · quiet/)).toBeVisible();
  // leave the shared demo daemon as found (the watches spec expects none)
  await page.getByRole('button', { name: 'Delete Zoho mentions' }).click();
  await expect(page.getByText(/· quiet/)).toHaveCount(0);
});
