// File links in the REAL app against real Claude, visibly: a harmless file opens in its default app, a script is
// only shown in its folder (never run). Opens windows on the desktop (the file's app, the file manager).
import { execSync as _x, spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, chmodSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const SLOW = Number(process.env.SLOW ?? 1500);
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_MODEL ??= 'claude-haiku-5-5';

// a folder with a note and a script; the script would leave a marker if it ever ran
const dir = mkdtempSync(join(tmpdir(), 'sb-links-'));
writeFileSync(join(dir, 'notes.md'), '# Notes\n\nOpened from a Switchboard file link.\n');
writeFileSync(join(dir, 'run.sh'), `#!/bin/sh\ntouch ${join(dir, 'RAN')}\n`);
chmodSync(join(dir, 'run.sh'), 0o755);

const step = async (m) => { console.log('·', m); await sleep(SLOW); };
const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer('http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
const toast = async () => (await d.executeScript("return document.querySelector('.toast')?.textContent ?? ''")).trim();
let ok = true;

try {
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await step(`creating a session in ${dir}`);
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//button[@role='tab' and normalize-space()='New session']")).click();
  const folder = await x("//label[starts-with(normalize-space(),'Folder on')]//input");
  await folder.clear(); await folder.sendKeys(dir);
  await (await x("//label[starts-with(normalize-space(),'Name in the tree')]//input")).sendKeys('File links check');
  await (await x("//button[normalize-space()='Create session']")).click();
  await x("//div[@role='log']");
  await step('asking Claude to name the two files');
  const box = await x("//textarea");
  await box.sendKeys('Without using any tools, reply with exactly this line: Open `notes.md` and `run.sh`.', Key.ENTER);
  const note = await x("//code[contains(@class,'path') and normalize-space()='notes.md']", 120000);
  const script = await x("//code[contains(@class,'path') and normalize-space()='run.sh']");

  await step('clicking notes.md: it should open in its default app');
  await note.click();
  await sleep(2000);
  const t1 = await toast();
  console.log('  notice:', JSON.stringify(t1));
  if (t1) { ok = false; console.log('  ✗ expected it to open with no notice'); }

  await step('clicking run.sh: it should only be shown in its folder');
  await script.click();
  await sleep(2000);
  const t2 = await toast();
  console.log('  notice:', JSON.stringify(t2));
  if (!t2.startsWith('Shown in its folder')) { ok = false; console.log('  ✗ expected "Shown in its folder"'); }
  await sleep(1500);
  if (existsSync(join(dir, 'RAN'))) { ok = false; console.log('  ✗ THE SCRIPT RAN'); } else console.log('  ✓ the script did not run');

  await step('tidying up: removing the session from the tree');
  await (await x("//button[normalize-space()='Remove from tree']")).click();
} catch (e) { ok = false; console.log('✗', e.message); }
finally { await d.quit(); proc.kill(); }
console.log(ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);
