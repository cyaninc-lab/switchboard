import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { DAEMON_PORT, DATA } from '../playwright.config';

const daemon = process.env.SB_DAEMON ?? '../.venv/bin/switchboard-daemon';

test('questions, plan approval, and allow for this session', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const add = page.getByRole('form', { name: 'Add to Switchboard' });
  await add.getByRole('button', { name: /Deploy #52 rolled back/ }).click();
  await add.getByRole('button', { name: 'Add to tree' }).click();
  const input = page.getByLabel('Message this session');
  const log = page.getByRole('log', { name: 'Session history' });
  const say = async (t: string) => { await input.fill(t); await input.press('Enter'); };

  // a clarifying question: one click answers it
  await say('ask me something');
  const q = page.getByRole('group', { name: 'Claude asks' });
  await expect(q).toContainText('Which database should I use?');
  await expect(q.getByRole('radio', { name: /SQLite/ })).toContainText('One file, no server');
  await page.screenshot({ path: 'e2e/.results/question.png' });
  await q.getByRole('radio', { name: /Postgres/ }).click();
  await expect(log).toContainText("You picked: {'Which database should I use?': 'Postgres'}");
  await expect(q).toHaveCount(0);
  // ...or in your own words
  await say('ask me again');
  await q.getByLabel(/Other answer to/).fill('whatever is simplest');
  await q.getByRole('button', { name: 'Answer' }).click();
  await expect(log).toContainText("'whatever is simplest'");

  // plan approval: send it back with feedback, then approve
  await say('make a plan');
  const plan = page.getByRole('group', { name: 'Plan to approve' });
  await expect(plan.getByRole('heading', { name: 'Plan' })).toBeVisible();  // rendered markdown, not raw
  await expect(plan).toContainText('Write the migration');
  await page.screenshot({ path: 'e2e/.results/plan.png' });
  await plan.getByLabel('What to change in the plan').fill('use Alembic');
  await plan.getByRole('button', { name: 'Keep planning' }).click();
  await expect(log).toContainText('Keep planning. The user says: use Alembic');
  await say('plan again');
  await plan.getByRole('button', { name: 'Approve plan' }).click();
  await expect(log).toContainText('Plan approved, starting.');

  // tool permission: allow for this session
  await say('run the checks');
  const perm = page.getByRole('group', { name: 'Permission needed' });
  await expect(perm.getByRole('button', { name: 'Always allow' })).toBeVisible();
  await perm.getByRole('button', { name: 'Allow for this session' }).click();
  await expect(log).toContainText('(remembered for: session)');

  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await page.getByRole('button', { name: 'Remove from tree' }).click();
});
