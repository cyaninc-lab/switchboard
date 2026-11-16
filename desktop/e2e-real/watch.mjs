// Drives the REAL desktop app (release build) through tauri-driver/WebDriver, visibly and slowly enough to
// watch, against real Claude. No mouse or keyboard of the user's is used.
// Usage: node e2e-real/watch.mjs "<session search text>" "<dir to expect the agent to create>"
// Env: SWITCHBOARD_MODEL (default claude-haiku-5-5), SLOW (ms between steps, default 1200)
import { execSync as _x } from 'node:child_process';
import { spawn } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const [search = 'Sleep-and-touch', expectDir = ''] = process.argv.slice(2);
const SLOW = Number(process.env.SLOW ?? 1200);
// a run that crashed can leave a WebDriver session holding the only slot
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_MODEL ??= 'claude-haiku-5-5';

const step = async (msg) => { console.log('·', msg); await sleep(SLOW); };
const driverProc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } })
  .usingServer('http://127.0.0.1:4444/').build();
const x = (xp) => d.wait(until.elementLocated(By.xpath(xp)), 30000);
const shot = async (name) => writeFileSync(`e2e/.results/real-${name}.png`, await d.takeScreenshot(), 'base64');

try {
  await step('app open: waiting for this machine to connect by itself');
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await step('connected; opening Add');
  await (await x("//button[normalize-space()='+ Add']")).click();
  const box = await x("//input[@type='search' and contains(@placeholder,'Search sessions')]");
  await step(`searching for "${search}"`);
  await box.sendKeys(search);
  await sleep(800);
  await (await x(`//button[contains(@class,'srow') and contains(., '${search}')]`)).click();
  await step('adding it to the tree');
  await (await x("//button[normalize-space()='Add to tree']")).click();
  await x("//div[@role='log']");
  await step('history loaded; sending a message to real Claude');
  const input = await x("//label[contains(., 'Message this session')]//input");
  const dir = expectDir || `sb-window-${Date.now()}`;
  await input.sendKeys(`Use the Bash tool to run exactly: mkdir ${dir} . Then reply with just DONE.`, Key.ENTER);
  const card = await d.wait(until.elementLocated(By.xpath("//div[@aria-label='Permission needed']")), 180000);
  await step('Claude asks permission: here is the card');
  await shot('permission');
  await sleep(SLOW * 2);
  await (await card.findElement(By.xpath(".//button[normalize-space()='Allow']"))).click();
  await step('allowed; waiting for the reply');
  await d.wait(until.elementLocated(By.xpath("//button[contains(@class,'entry') and @title='Live and ready']")), 180000);
  await sleep(SLOW);
  await shot('done');
  const log = await (await x("//div[@role='log']")).getText();
  console.log('reply contains DONE:', /DONE/.test(log));
  await step('stopping the live session (it stays in the tree)');
  await (await x("//button[normalize-space()='Stop']")).click();
  await d.wait(until.elementLocated(By.xpath("//button[contains(@class,'entry') and starts-with(@title,'Stopped')]")), 30000);
  await step('removing the test entry from the tree');
  await (await x("//button[normalize-space()='Remove from tree']")).click();
  await sleep(SLOW);
  console.log('dir name used:', dir);
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await sleep(SLOW);
  await d.quit().catch(() => {});
  driverProc.kill();
}
