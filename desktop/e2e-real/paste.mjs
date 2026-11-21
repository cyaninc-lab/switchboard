// Pasting an image into the REAL desktop app (WebKitGTK): Ctrl+V in the message box with an image on the system
// clipboard must attach it. Put an image on the clipboard first (any app's "Copy image", or xclip). Nothing is sent.
import { execSync as _x, spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, Key, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {}
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_NO_SERVICE_REFRESH = '1';  // a test build must never put its daemon into the real background service
const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer('http://127.0.0.1:4444/').build();
const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
let ok = false;
try {
  await (await x("//nav//button[contains(@class,'entry')]")).click();  // any session: nothing is sent
  const box = await x("//div[contains(@class,'composer') or ancestor::form]//textarea | //textarea");
  await box.click();
  console.log('· Ctrl+V in the message box');
  await box.sendKeys(Key.chord(Key.CONTROL, 'v'));
  const thumb = await x("//span[contains(@class,'thumb')]/img", 8000).catch(() => null);
  ok = !!thumb;
  console.log(ok ? `· attached: ${await thumb.getAttribute('alt')} (${(await thumb.getAttribute('src')).length} chars of image data)` : '· nothing attached');
  writeFileSync('e2e/.results/real-paste.png', await d.takeScreenshot(), 'base64');
  if (thumb) await (await x("//span[contains(@class,'thumb')]/button")).click();  // take it off again
} finally {
  await d.quit(); proc.kill();
}
process.exit(ok ? 0 : 1);
