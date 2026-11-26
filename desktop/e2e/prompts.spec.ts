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
  await expect(log).toContainText('AskUserQuestion(Which database should I use?)');  // the chat line names the question
  // the header label stays on one line, even squeezed in a narrow window
  await page.setViewportSize({ width: 760, height: 820 });
  const chip = q.locator('.chip');
  expect((await chip.boundingBox())!.height).toBeLessThan(24);
  await page.setViewportSize({ width: 1280, height: 820 });
  // options' previews (Claude Code's side box): the first one's at first, the one pointed at on hover
  const preview = q.getByRole('region', { name: 'Preview for: Which database should I use?' });
  await expect(preview).toHaveText('+ data/app.db\n  tables: users, posts');  // as written: lines and spacing kept
  await q.getByRole('radio', { name: /Postgres/ }).hover();
  await expect(preview).toContainText('compose.yml: db service');
  const [o, pv] = [await q.getByRole('radio', { name: /SQLite/ }).boundingBox(), await preview.boundingBox()];
  expect(pv!.x).toBeGreaterThan(o!.x + o!.width - 1);  // beside the options, not under them
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

  await page.getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Close session' }).click();  // its tab closes with it
  await page.getByRole('navigation', { name: 'Sessions' }).getByRole('button', { name: /Deploy #52 rolled back/ }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Remove from tree' }).click();
});

test('pick one shows round marks, pick any square ones; pick any can be sent with nothing ticked', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Connect a machine' }).click();
  await page.getByLabel('Daemon address').fill(`http://127.0.0.1:${DAEMON_PORT}`);
  await page.getByLabel('One-time code').fill(execFileSync(daemon, ['--data', DATA, 'pair']).toString().trim());
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('button', { name: '+ Add' }).click();
  const dialog = page.getByRole('form', { name: 'Add to Switchboard' });
  await dialog.getByRole('tab', { name: 'New session' }).click();
  await dialog.getByLabel(/Folder on/).fill('/tmp');
  await dialog.getByLabel('Name in the tree').fill('Questions');
  await dialog.getByRole('button', { name: 'Create session' }).click();
  const box = page.getByLabel('Message this session'), log = page.getByRole('log', { name: 'Session history' });
  await box.fill('ask me several'); await box.press('Enter');
  const checks = page.getByRole('group', { name: 'Which checks should run?' });
  await expect(checks.getByRole('checkbox')).toHaveCount(2);
  await expect(checks.locator('.mark.box')).toHaveCount(2);  // square: pick any
  await page.screenshot({ path: 'e2e/.results/question-multi.png' });
  await page.getByRole('button', { name: 'Answer' }).click();  // nothing ticked: still an answer
  await expect(log).toContainText('You picked: ');
  await expect(log).toContainText('None of these');
  await box.fill('ask me'); await box.press('Enter');
  const db = page.getByRole('radiogroup', { name: 'Which database should I use?' });
  await expect(db.locator('.mark:not(.box)')).toHaveCount(2);  // round: pick one
});
