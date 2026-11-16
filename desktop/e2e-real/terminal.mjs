// Shows the real-terminal toggle in the REAL app: the actual claude TUI for a session, then back to chat.
import { execSync as _x } from 'node:child_process';
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

// SB_APP / SB_DRIVER_URL: drive an app on another machine (e.g. Windows over an SSH tunnel to its tauri-driver)
const APP = process.env.SB_APP ?? new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const DRIVER = process.env.SB_DRIVER_URL;
const search = process.argv[2] ?? 'Sleep-and-touch';
const SLOW = Number(process.env.SLOW ?? 1500);
if (!DRIVER) try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_MODEL ??= 'claude-haiku-5-5';
const step = async (m) => { console.log('·', m); await sleep(SLOW); };
const proc = DRIVER ? null : spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
if (!DRIVER) await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer(DRIVER ?? 'http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
const shot = async (n) => writeFileSync(`e2e/.results/real-${n}.png`, await d.takeScreenshot(), 'base64');
const screen = async () => (await x("//div[contains(@class,'xterm-rows')]")).getText();
const waitScreen = async (re, t = 90000) => { const end = Date.now() + t; while (Date.now() < end) { const s = await screen(); if (re.test(s)) return s; await sleep(500); } throw new Error('terminal never showed ' + re); };

try {
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await step('adding a session');
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//input[@type='search' and contains(@placeholder,'Search sessions')]")).sendKeys(search);
  await sleep(800);
  const row = await x(`//button[contains(@class,'srow') and contains(., '${search}')]`);
  if (await row.isEnabled()) {
    await row.click();
    await (await x("//button[normalize-space()='Add to tree']")).click();
  } else {  // already in the tree from an earlier run: open it from there, by its title
    const title = (await (await row.findElement(By.css('.stitle'))).getText()).replace(/in tree$/, '').trim().slice(0, 20);
    await (await x("//button[normalize-space()='Cancel']")).click();
    await (await x(`//button[contains(@class,'entry') and contains(., '${title}')]`)).click();
  }
  await x("//div[@role='log']");
  await step('switching to the real terminal');
  await (await x("//button[normalize-space()='Terminal']")).click();
  await x("//div[contains(@class,'xterm-rows')]");
  await waitScreen(/>|╭|Claude|\?/);
  const keys = await x("//textarea[contains(@class,'xterm-helper-textarea')]");
  if (/trust this folder/.test(await screen())) {
    // Claude Code's own first-run prompt; only ever answered for this script's throwaway /tmp/sb-* folders
    const testDir = process.env.SB_TEST_DIR ?? '/tmp/sb-';  // only ever this script's throwaway folder
    if (!(await screen()).replace(/\s+/g, '').includes(testDir.replace(/\s+/g, ''))) throw new Error('trust prompt for a non-test folder: not answering it');
    await step("Claude Code asks whether to trust the folder (it's a throwaway test folder): Yes");
    await shot('tui-trust');
    await keys.sendKeys(Key.ARROW_DOWN);
    await sleep(500);
    await keys.sendKeys(Key.ENTER);
    await waitScreen(/^(?![\s\S]*trust this folder)[\s\S]*Claude Code/, 30000);
  }
  await step('the actual Claude Code TUI is running for this session');
  await shot('tui-open');
  await keys.sendKeys('Reply with just: HELLO FROM THE REAL TERMINAL');
  await sleep(600);
  await keys.sendKeys(Key.ENTER);
  await step('sent a message in the TUI; waiting for the reply');
  await waitScreen(/HELLO FROM THE REAL TERMINAL[\s\S]*HELLO FROM THE REAL TERMINAL/, 120000);
  await sleep(SLOW);
  await shot('tui-reply');
  await step('opening the slash-command menu');
  await keys.sendKeys('/');
  await sleep(SLOW * 2);
  await shot('tui-slash');
  await keys.sendKeys(Key.ESCAPE);
  await sleep(SLOW);
  await step('back to the chat view (the TUI closes; history now includes what was said there)');
  await (await x("//button[normalize-space()='Chat']")).click();
  await x("//div[@role='log' and contains(., 'HELLO FROM THE REAL TERMINAL')]", 30000);
  await sleep(SLOW * 2);
  await shot('back-to-chat');
  await (await x("//button[normalize-space()='Remove from tree']")).click();
  await sleep(SLOW);
  console.log('PASS terminal toggle');
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('terminal-failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await d.quit().catch(() => {});
  proc?.kill();
}
