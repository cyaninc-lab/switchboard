// A watch wakes a real agent in the REAL app; its reply lands in the inbox. Paced to watch.
import { execSync as _x, spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, until } from 'selenium-webdriver';

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
try {
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//input[@type='search' and contains(@placeholder,'Search sessions')]")).sendKeys(search);
  await sleep(800);
  const row = await x(`//button[contains(@class,'srow') and contains(., '${search}')]`);
  let title;
  if (await row.isEnabled()) { await row.click(); await (await x("//button[normalize-space()='Add to tree']")).click(); }
  else {
    title = (await (await row.findElement(By.css('.stitle'))).getText()).replace(/in tree$/, '').trim().slice(0, 20);
    await (await x("//button[normalize-space()='Cancel']")).click();
    await (await x(`//button[contains(@class,'entry') and contains(., '${title}')]`)).click();
  }
  await x("//div[@role='log']");
  // textContent, not getText: WebKit's driver returns '' for the ellipsis-truncated label
  const entryName = (await (await x("//button[contains(@class,'entry') and contains(@class,'sel')]//span[contains(@class,'ename')]")).getAttribute('textContent')).trim();
  await step(`session "${entryName}" in the tree; opening Watches`);
  await (await x("//button[normalize-space()='Watches']")).click();
  await (await x("//button[normalize-space()='New watch']")).click();
  await (await x("//form[@aria-label='Edit watch']//label[contains(., 'Name')]//input")).sendKeys('Disk space');
  await (await x("//button[@role='radio' and normalize-space()='Command']")).click();
  await (await x("//label[contains(., 'Command')]//input")).sendKeys('df -h /');
  await (await x("//button[@role='radio' and normalize-space()='Wake an agent']")).click();
  const sel = await x("//label[contains(., 'Session to wake')]//select");
  for (const o of await sel.findElements(By.css('option'))) {
    if ((await o.getAttribute('textContent')).trim() === entryName) { await o.click(); break; }
  }
  await (await x("//label[contains(., 'Instructions for the agent')]//textarea")).sendKeys('In one short sentence: how full is the disk, and is that a problem?');
  await step('a command watch (df -h /) that wakes this agent');
  await shot('watch-form');
  await (await x("//button[normalize-space()='Save watch']")).click();
  const wrow = await x("//li[contains(., 'Disk space')]");
  await step('saved; pressing Test (fires it now, as if the output changed)');
  await (await wrow.findElement(By.xpath(".//button[normalize-space()='Test']"))).click();
  await sleep(SLOW);
  await (await x("//button[normalize-space()='Close']")).click();
  await step('the agent is working; its reply will land in the inbox');
  const reply = await x("//article[contains(@aria-label, 'Disk space: agent replied')]", 180000);
  await sleep(SLOW);
  await shot('watch-inbox');
  console.log('inbox reply:', (await reply.getText()).replace(/\n/g, ' | ').slice(0, 300));
  // tidy: delete the watch, stop and remove the session
  await (await x("//button[normalize-space()='Watches']")).click();
  await (await x("//button[@aria-label='Delete Disk space']")).click();
  await (await x("//button[normalize-space()='Close']")).click();
  await (await x("//button[normalize-space()='Stop']")).click().catch(() => {});
  await sleep(SLOW);
  await (await x("//button[normalize-space()='Remove from tree']")).click();
  console.log('PASS watch woke the agent; reply in the inbox');
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('watch-failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await d.quit().catch(() => {});
  proc?.kill();
}
