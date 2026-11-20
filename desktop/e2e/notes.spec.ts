import { test, expect } from '@playwright/test';
import { render } from '../src/lib/api';

// Claude Code's own messages that arrive as "user" messages (no page needed)
test('a background task notification shows as a step, not as something the user said', () => {
  const t = `<task-notification>
<task-id>bbsahcfch</task-id>
<tool-use-id>toolu_01KTjaVfn2zdt5afBjiCeg37</tool-use-id>
<output-file>/tmp/claude-1000/x/tasks/bbsahcfch.output</output-file>
<status>completed</status>
<summary>Background command "Run a 4-minute timer, printing progress every 30s" completed (exit code 0)</summary>
</task-notification>`;
  const [line] = render({ type: 'user', uuid: 'u1', message: { role: 'user', content: t } });
  expect(line).toMatchObject({ kind: 'tool', name: 'TaskNotification', text: 'Background command "Run a 4-minute timer, printing progress every 30s" completed (exit code 0)' });
  // the same words typed by the user stay the user's
  const [mine] = render({ type: 'user', uuid: 'u2', message: { role: 'user', content: 'what does <task-notification> mean?' } });
  expect(mine).toMatchObject({ kind: 'text' });
});
