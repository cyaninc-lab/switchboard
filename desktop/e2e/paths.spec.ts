import { test, expect } from '@playwright/test';
import { looksLikePath } from '../src/lib/markdown';

// which inline code in Claude's replies becomes a clickable file link (no page needed)
test('file paths are recognised, other code is not', () => {
  for (const t of ['server/app.py', 'server/app.py:212', '/home/u/notes.md', '~/Projects/x', 'C:\\Tools\\rig\\server.js', 'README.md',
                   'package.json', 'src/lib/', './build.sh', 'docs\\HANDOVER.md'])
    expect(looksLikePath(t), t).toBe(true);
  for (const t of ['npm run check', 'foo()', 'x.map(f)', 'https://example.com/a.md', '3.14', 'v1.2', 'a < b', '...', 'GET /api/feed', 'true', 'dash_url'])
    expect(looksLikePath(t), t).toBe(false);
});
