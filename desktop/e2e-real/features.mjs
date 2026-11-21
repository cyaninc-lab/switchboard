// Images + slash commands in the REAL app against real Claude, visibly. The image arrives through the
// drop handler (WebDriver can't type into a hidden file input).
import { execSync as _x, spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { deflateSync, crc32 } from 'node:zlib';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const search = process.argv[2] ?? 'Sleep-and-touch';
const SLOW = Number(process.env.SLOW ?? 1500);
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_NO_SERVICE_REFRESH = '1';  // a test build must never put its daemon into the real background service
env.SWITCHBOARD_MODEL ??= 'claude-haiku-5-5';

function png(w, h, [r, g, b]) {  // a solid-colour PNG, no dependencies
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(Buffer.concat([Buffer.from(t), d])) >>> 0); return Buffer.concat([len, Buffer.from(t), d, c]); };
  const hdr = Buffer.alloc(13); hdr.writeUInt32BE(w, 0); hdr.writeUInt32BE(h, 4); hdr.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array(w).fill([r, g, b]).flat())]);
  return Buffer.concat([Buffer.from([0x89, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', hdr), chunk('IDAT', deflateSync(Buffer.concat(Array(h).fill(row)))), chunk('IEND', Buffer.alloc(0))]);
}

const step = async (m) => { console.log('·', m); await sleep(SLOW); };
const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer('http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
const shot = async (n) => writeFileSync(`e2e/.results/real-${n}.png`, await d.takeScreenshot(), 'base64');

try {
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  await (await x("//button[normalize-space()='+ Add']")).click();
  await (await x("//input[@type='search' and contains(@placeholder,'Search sessions')]")).sendKeys(search);
  await sleep(800);
  const row = await x(`//button[contains(@class,'srow') and contains(., '${search}')]`);
  if (await row.isEnabled()) { await row.click(); await (await x("//button[normalize-space()='Add to tree']")).click(); }
  else { await (await x("//button[normalize-space()='Cancel']")).click(); await (await x(`//button[contains(@class,'entry') and contains(., '${search}')]`)).click(); }
  await x("//div[@role='log']");
  await step('dropping an image into the chat (a purple square)');
  await d.executeScript(`
    const bytes = Uint8Array.from(atob(arguments[0]), c => c.charCodeAt(0));
    const dt = new DataTransfer(); dt.items.add(new File([bytes], 'square.png', { type: 'image/png' }));
    document.querySelector('form.inputbar').dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  `, png(96, 96, [203, 166, 247]).toString('base64'));
  await x("//*[@aria-label='Attached images']//img");
  const input = await x("//label[contains(., 'Message this session')]//input");
  await input.sendKeys('What colour is this image? Reply with one word.');
  await step('thumbnail attached, sending');
  await shot('image-attached');
  await input.sendKeys(Key.ENTER);
  await d.wait(until.elementLocated(By.xpath("//button[contains(@class,'entry') and @title='Live and ready']")), 180000);
  await sleep(SLOW);
  await shot('image-reply');
  const reply = await (await x("//div[@role='log']")).getText();
  console.log('reply tail:', reply.split('\n').slice(-2).join(' | '));
  await step('typing / for the command menu (the session sent its real command list)');
  await input.sendKeys('/');
  await x("//*[@role='listbox' and @aria-label='Slash commands']");
  await sleep(SLOW * 2);
  await shot('slash-menu');
  await input.sendKeys('cont');
  await sleep(SLOW);
  await input.sendKeys(Key.TAB);
  await step('completed to /context, sending');
  await input.sendKeys(Key.ENTER);
  await x("//div[@role='log' and contains(., 'Context Usage')]", 120000);
  await sleep(SLOW * 2);
  await shot('context');
  await (await x("//button[normalize-space()='Stop']")).click();
  await sleep(SLOW);
  await (await x("//button[normalize-space()='Remove from tree']")).click();
  await sleep(SLOW);
  console.log('PASS images + slash commands');
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('features-failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await d.quit().catch(() => {});
  proc.kill();
}
