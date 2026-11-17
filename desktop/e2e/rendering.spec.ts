import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('diffs, to-dos, expandable output and the context/cost meter', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const add = page.getByRole('form', { name: 'Add to Switchboard' });
  await add.getByRole('button', { name: /Deploy #52 rolled back/ }).click();
  await add.getByRole('button', { name: 'Add to tree' }).click();
  const log = page.getByRole('log', { name: 'Session history' });

  // an edit as a diff: unchanged context, the removed line and the added line
  const diff = log.getByLabel('Edit(server/app.py)');
  await expect(diff.locator('.dl.del')).toHaveText("-    dash = v['dash_url']");
  await expect(diff.locator('.dl.add')).toHaveText("+    dash = v.get('dash_url')");
  await expect(diff.locator('.dl:not(.add):not(.del)')).toHaveCount(2);
  // the to-do list as a checklist; its "modified successfully" result is hidden
  const todos = log.getByRole('list', { name: 'To-dos' });
  await expect(todos.locator('li.completed')).toHaveText(/Find the cause/);
  await expect(todos.locator('li.in_progress')).toHaveText(/Fixing the handler/);
  await expect(log).not.toContainText('Todos have been modified');
  // long tool output: 6 lines, then expand
  await expect(log).toContainText('test_6 PASSED');
  await expect(log).not.toContainText('test_20 PASSED');
  await log.getByRole('button', { name: 'Show all 20 lines' }).click();
  await expect(log).toContainText('test_20 PASSED');
  await page.screenshot({ path: 'e2e/.results/rendering.png' });

  // context and cost after a turn
  const input = page.getByLabel('Message this session');
  await input.fill('hello');
  await input.press('Enter');
  await expect(page.getByRole('contentinfo')).toContainText('ctx 26% · ≈$0.01');

  // thinking (collapsed) and a subagent's work grouped under its Agent step
  await input.fill('think, then use a subagent');
  await input.press('Enter');
  const agent = log.locator('details.agent').filter({ hasText: 'Agent: Search the codebase' });
  await expect(agent).toContainText('3 steps');
  await expect(log.locator('details.thinking').last()).toContainText('Thinking');
  await expect(agent.getByText('Found it in server/app.py line 212.')).toBeHidden();  // collapsed until opened
  await agent.locator('summary').click();
  await expect(agent.getByText('Found it in server/app.py line 212.')).toBeVisible();
  await expect(agent).toContainText('Grep(dash_url)');
  await page.screenshot({ path: 'e2e/.results/subagent.png' });

  // effort: the model offers levels; the next message runs at the chosen one
  await page.getByRole('contentinfo').getByLabel('Effort').selectOption('low');
  await input.fill('what effort');
  await input.press('Enter');
  await expect(log).toContainText('(effort: low)');

  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await page.getByRole('button', { name: 'Remove from tree' }).click();
});
