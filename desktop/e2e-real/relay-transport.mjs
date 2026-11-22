// The desktop app reaches machines through the relay with its Rust-side transport (Tauri http/websocket plugins):
// a relay machine's sessions load and its live connection says hello. Uses this computer's signed-in account.
import { execSync as _x, spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, until } from 'selenium-webdriver';
const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
try { _x('pkill -x tauri-driver; pkill -x WebKitWebDriver'); } catch {}
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
env.SWITCHBOARD_NO_SERVICE_REFRESH = '1';  // a test build must never put its daemon into the real background service
const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
await sleep(1500);
const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } }).usingServer('http://127.0.0.1:4444/').build();
let ok = false;
try {
  const name = process.argv[2] ?? 'yagoo';
  const head = await d.wait(until.elementLocated(By.xpath(`//nav//*[contains(@class,'mname') or self::h3 or self::span][normalize-space()='${name}']`)), 40000).catch(() => null);
  await sleep(8000);  // a poll and the live connection
  const html = await d.executeScript(`return document.querySelector('nav')?.innerText ?? ''`);
  const offline = await d.executeScript(`return [...document.querySelectorAll('.offnote')].map((e) => e.textContent)`);
  const transport = await d.executeScript(`return typeof window.__TAURI_INTERNALS__ !== 'undefined'`);
  console.log('· tauri:', transport, '| machine heading found:', !!head);
  console.log('· can\'t-reach notes:', JSON.stringify(offline));
  ok = !!head && !offline.some((t) => t.startsWith(name));
  console.log(ok ? `· ${name} loads through the relay` : `· ${name} did not load`);
} finally { await d.quit(); proc.kill(); }
process.exit(ok ? 0 : 1);
