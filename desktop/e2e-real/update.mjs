// Self-update, for real: the installed AppImage (an older release) finds the newer one, shows the banner,
// downloads it, checks its signature, replaces itself and restarts. Run with the app installed at SB_APPIMAGE.
import { execSync as _x, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, until } from 'selenium-webdriver';

const APPIMAGE = process.env.SB_APPIMAGE ?? `${process.env.HOME}/Applications/Switchboard.AppImage`;
const SLOW = Number(process.env.SLOW ?? 2000);
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex');
const before = sha(APPIMAGE);
const latest = await (await fetch('https://github.com/cyaninc-lab/switchboard-releases/releases/latest/download/latest.json')).json();
console.log(`installed ${before.slice(0, 12)}…, latest release ${latest.version}`);

const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APPIMAGE } }).usingServer('http://127.0.0.1:4444/').build();
const shot = async (n) => writeFileSync(`e2e/.results/real-${n}.png`, await d.takeScreenshot(), 'base64');
try {
  console.log('· app open; it checks for updates 10 s after start');
  const bar = await d.wait(until.elementLocated(By.css('.updbar')), 60000);
  console.log('· banner:', (await bar.getAttribute('textContent')).replace(/\s+/g, ' ').trim());
  await sleep(SLOW);
  await shot('update-banner');
  const notes = await d.findElements(By.xpath("//div[contains(@class,'updbar')]//button[normalize-space()=\"What's new\"]"));
  if (notes.length) { await notes[0].click(); await sleep(SLOW); await shot('update-notes'); }
  console.log('· clicking Restart to update');
  await (await d.findElement(By.xpath("//button[normalize-space()='Restart to update']"))).click();
  // it downloads, verifies, replaces the AppImage, then relaunches (which ends this WebDriver session)
  for (let i = 0; i < 90; i++) {
    await sleep(1000);
    try { const t = await (await d.findElement(By.css('.updbar'))).getAttribute('textContent'); if (i % 5 === 0) console.log('  ', t.replace(/\s+/g, ' ').trim().slice(0, 80)); }
    catch { console.log('· the app restarted'); break; }
  }
} catch (e) {
  console.error('FAILED:', e.message);
  await shot('update-failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await d.quit().catch(() => {});
  proc.kill();
}
await sleep(3000);
const after = sha(APPIMAGE);
const asset = latest.platforms['linux-x86_64-appimage'] ?? latest.platforms['linux-x86_64'];
const fresh = createHash('sha256').update(Buffer.from(await (await fetch(asset.url)).arrayBuffer())).digest('hex');
console.log(`AppImage changed: ${before !== after} | now matches the ${latest.version} release: ${after === fresh}`);
if (after !== fresh) process.exitCode = 1;
