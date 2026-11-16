// New session from the Add dialog in the REAL app, against real Claude; a second message sent mid-turn queues.
import { execSync as _x, spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

// SB_APP / SB_DRIVER_URL: drive an app on another machine (e.g. Windows over an SSH tunnel to its tauri-driver)
const APP = process.env.SB_APP ?? new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const DRIVER = process.env.SB_DRIVER_URL;
const SLOW = Number(process.env.SLOW ?? 1500);
if (!DRIVER) try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_MODEL ??= 'claude-haiku-5-5';
const folder = process.env.SB_FOLDER ?? mkdtempSync('/tmp/sb-new-');
const step = async (m) => { console.log('·', m); await sleep(SLOW); };
const proc = DRIVER ? null : spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
if (!DRIVER) await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer(DRIVER ?? 'http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
const shot = async (n) => writeFileSync(`e2e/.results/real-${n}.png`, await d.takeScreenshot(), 'base64');
try {
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await step('Add → New session');
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//button[@role='tab' and normalize-space()='New session']")).click();
  const f = await x("//label[contains(., 'Folder on')]//input");
  await f.clear(); await f.sendKeys(folder);
  await (await x("//label[contains(., 'Name in the tree')]//input")).sendKeys('Fresh from Switchboard');
  await step(`folder ${folder}`);
  await shot('new-dialog');
  await (await x("//button[normalize-space()='Create session']")).click();
  await x("//button[contains(@class,'entry') and starts-with(@title,'New')]");
  await step('created: a new entry, waiting for its first message');
  const input = await x("//label[contains(., 'Message this session')]//input");
  await input.sendKeys('In one short sentence, what is a telephone switchboard?', Key.ENTER);
  await sleep(700);
  await input.sendKeys('Now reply with just: SECOND', Key.ENTER);
  await step('second message sent while the first is running: it queues');
  await shot('queued');
  await x("//div[@role='log' and contains(., 'SECOND') and count(.//div[contains(@class,'md') and contains(., 'SECOND')]) > 0]", 180000);
  await d.wait(until.elementLocated(By.xpath("//button[contains(@class,'entry') and @title='Live and ready']")), 60000);
  await sleep(SLOW);
  await shot('new-done');
  console.log('log:', (await (await x("//div[@role='log']")).getText()).replace(/\n/g, ' | '));
  await (await x("//button[normalize-space()='Stop']")).click();
  await d.wait(until.elementLocated(By.xpath("//button[contains(@class,'entry') and starts-with(@title,'Stopped')]")), 30000);
  await step('stopped: it is now an ordinary session on disk');
  await (await x("//button[normalize-space()='Remove from tree']")).click();
  await sleep(SLOW);
  console.log('PASS new session');
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('new-failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await d.quit().catch(() => {});
  proc?.kill();
}
