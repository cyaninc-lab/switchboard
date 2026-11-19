import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('split view: a second session to the side or below, resizable, its draft comes back as a tab', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  page.on('pageerror', (e) => console.log('PAGEERR', e.message));
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const tree = page.getByRole('navigation', { name: 'Sessions' });
  for (const name of ['Split left', 'Split right']) {
    await page.getByRole('button', { name: '+ Add' }).click();
    const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
    await dialog.getByRole('tab', { name: 'New session' }).click();
    await dialog.getByLabel(/Folder on/).fill(mkdtempSync(join(tmpdir(), 'sb-split-')));
    await dialog.getByLabel('Name in the tree').fill(name);
    await dialog.getByRole('button', { name: 'Create session' }).click();
    await expect(tree.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  }
  // a draft in "Split right", then open it to the side of "Split left"
  await page.getByLabel('Message this session').fill('half typed');
  await tree.getByRole('button', { name: /Split left/ }).click();
  await tree.getByRole('button', { name: /Split right/ }).click({ button: 'right' });
  await page.getByRole('menu', { name: 'Split right actions' }).getByRole('menuitem', { name: 'Open to the side' }).click();
  const pane = page.getByRole('region', { name: 'Split: Split right' });

  await expect(pane).toBeVisible();
  const inner = page.frameLocator('iframe[title="Split: Split right"]');
  await expect(inner.getByLabel('Message this session')).toHaveValue('half typed');  // the draft went along
  await expect(page.getByRole('tablist', { name: 'Open sessions' }).getByRole('tab', { name: /Split right/ })).toHaveCount(0);
  await expect(tree.getByRole('button', { name: /Split right/ })).toContainText('◫');
  // side by side: the pane is to the right of the chat; it answers in its pane
  const main = page.locator('main.term'), box = async (l: any) => (await l.boundingBox())!;
  expect((await box(pane)).x).toBeGreaterThan((await box(main)).x + (await box(main)).width - 2);
  await inner.getByLabel('Message this session').press('Enter');
  await expect(inner.getByRole('log', { name: 'Session history' })).toContainText('Done: half typed');
  await page.screenshot({ path: 'e2e/.results/split-side.png' });
  // stacked below, then resized by dragging the divider
  await pane.getByRole('button', { name: 'Stack below' }).click();
  expect((await box(pane)).y).toBeGreaterThan((await box(main)).y + (await box(main)).height - 2);
  const before = (await box(pane)).height;
  const d = await box(page.getByRole('separator', { name: 'Resize the split' }));
  await page.mouse.move(d.x + d.width / 2, d.y + 2);
  await page.mouse.down();
  await page.mouse.move(d.x + d.width / 2, d.y - 150, { steps: 5 });
  await page.mouse.up();
  expect((await box(pane)).height).toBeGreaterThan(before + 100);
  await page.screenshot({ path: 'e2e/.results/split-below.png' });
  // remembered after a restart
  await page.reload();
  await expect(page.getByRole('region', { name: 'Split: Split right' })).toBeVisible();
  // close: back as a tab, with its draft
  await page.frameLocator('iframe[title="Split: Split right"]').getByLabel('Message this session').fill('typed in the split');
  await page.getByRole('region', { name: 'Split: Split right' }).getByRole('button', { name: 'Close split' }).click();
  await expect(page.getByRole('region', { name: /^Split:/ })).toHaveCount(0);
  await expect(page.getByRole('tablist', { name: 'Open sessions' }).getByRole('tab', { name: /Split right/, selected: true })).toBeVisible();
  await expect(page.getByLabel('Message this session')).toHaveValue('typed in the split');
});
