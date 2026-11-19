// Pop-out windows in the REAL app, visibly: a session pops out with its draft, lives only there, and comes back as a
// tab with its draft. Isolated from your own Switchboard: its own app data (XDG_*) and a demo daemon on another port.
import { execSync as _x, execFileSync, spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const DAEMON = new URL('../../.venv/bin/switchboard-daemon', import.meta.url).pathname;
const SLOW = Number(process.env.SLOW ?? 1200);
const PORT = 7899;
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const home = mkdtempSync(join(tmpdir(), 'sb-win-'));
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
Object.assign(env, { XDG_DATA_HOME: join(home, 'data'), XDG_CONFIG_HOME: join(home, 'config'), XDG_CACHE_HOME: join(home, 'cache') });
const data = join(home, 'daemon');
const demo = spawn(DAEMON, ['--data', data, 'serve', '--demo', '--port', String(PORT), '--name', 'demo'], { env, stdio: 'ignore' });
await sleep(1500);

const step = async (m) => { console.log('·', m); await sleep(SLOW); };
const driver = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer('http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
const val = async (el) => d.executeScript('return arguments[0].value', el);
let ok = true;
const check = (cond, what) => { console.log(cond ? '  ✓' : '  ✗', what); if (!cond) ok = false; };

try {
  const main = await d.getWindowHandle();
  await step('connecting the demo daemon');
  await (await x("//button[normalize-space()='+ Connect a machine']")).click();
  const addr = await x("//label[contains(.,'Daemon address')]//input");
  await addr.clear(); await addr.sendKeys(`http://127.0.0.1:${PORT}`);
  await (await x("//label[contains(.,'One-time code')]//input")).sendKeys(execFileSync(DAEMON, ['--data', data, 'pair']).toString().trim());
  await (await x("//button[normalize-space()='Connect']")).click();
  await x("//span[contains(@class,'chip') and contains(., 'demo') and contains(., 'online')]");

  await step('a new session with a draft');
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//button[@role='tab' and normalize-space()='New session']")).click();
  const folder = await x("//label[starts-with(normalize-space(),'Folder on')]//input");
  await folder.clear(); await folder.sendKeys(mkdtempSync(join(tmpdir(), 'sb-win-s-')));
  await (await x("//label[starts-with(normalize-space(),'Name in the tree')]//input")).sendKeys('Window one');
  await (await x("//button[normalize-space()='Create session']")).click();
  await (await x("//textarea")).sendKeys('going along');

  await step('Pop out');
  await (await x("//button[normalize-space()='Pop out']")).click();
  await d.wait(async () => (await d.getAllWindowHandles()).length === 2, 15000);
  const win = (await d.getAllWindowHandles()).find((h) => h !== main);
  check(await d.executeScript("return !document.querySelector('.tabs .tab')"), 'its tab left the main window');
  check((await d.findElements(By.xpath("//nav//button[contains(.,'Window one') and contains(.,'⧉')]"))).length === 1, 'the tree marks it as in its own window');

  await d.switchTo().window(win);
  await step('in its window: the draft came along; send it');
  const box = await x("//textarea");
  await d.wait(async () => (await val(box)) === 'going along', 10000).catch(() => {});
  check((await val(box)) === 'going along', 'the draft came along');
  check(await d.executeScript("return getComputedStyle(document.querySelector('nav.tree')).display === 'none' && !document.querySelector('.tabs')"), 'just the session: no tree, no tabs');
  await box.sendKeys(Key.ENTER);
  await x("//div[@role='log']//*[contains(., 'Done: going along')]", 20000);
  check(true, 'it answers in its window');
  await (await x("//button[normalize-space()='Keep on top']")).click();
  await step('Keep on top on; a new draft; Back to tabs');
  await box.sendKeys('coming back');
  await (await x("//button[normalize-space()='Back to tabs']")).click();
  await d.switchTo().window(main);
  await d.wait(async () => (await d.getAllWindowHandles()).length === 1, 15000);
  check(true, 'the window closed');
  const tab = await x("//div[@role='tablist']//button[@role='tab' and contains(., 'Window one')]", 10000);
  check(!!tab, 'it came back as a tab');
  await d.wait(async () => (await val(await x('//textarea'))) === 'coming back', 10000).catch(() => {});
  check((await val(await x('//textarea'))) === 'coming back', 'with its draft');
  check((await d.findElements(By.xpath("//nav//button[contains(.,'Window one') and contains(.,'⧉')]"))).length === 0, 'the tree no longer marks it');
  await step('pop out again, then close the main window: the whole app closes');
  await (await x("//button[normalize-space()='Pop out']")).click();
  await d.wait(async () => (await d.getAllWindowHandles()).length === 2, 15000);
  // close it as a person would (the window manager's close request, like its × button); only this test app's window
  const pid = _x(`pgrep -f '^${APP}'`).toString().trim().split('\n')[0];
  const wid = _x('wmctrl -lp').toString().split('\n').map((l) => l.split(/\s+/)).find((c) => c[2] === pid && c.slice(4).join(' ') === 'Switchboard')?.[0];
  check(!!wid, `found the test app's main window (pid ${pid})`);
  _x(`wmctrl -i -c ${wid}`);
  const gone = await (async () => { for (let i = 0; i < 20; i++) { try { _x(`pgrep -f '^${APP}'`); } catch { return true; } await sleep(500); } return false; })();
  check(gone, 'the app exited, popped-out window and all');
  await step('done');
} catch (e) { ok = false; console.log('  ✗', e.message); }
finally {
  await d.quit().catch(() => {});
  driver.kill(); demo.kill();
}
console.log(ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);
