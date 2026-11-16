// Drives the REAL app (release build) through the background-service handover, visibly:
// app's own daemon -> toggle on -> systemd user service -> close app (agents keep a daemon) -> reopen (uses the service)
// -> toggle off -> back to the app's own daemon. Shell checks confirm each step. No user mouse/keyboard.
import { execSync as _x } from 'node:child_process';
import { spawn, execSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { Builder, By, until } from 'selenium-webdriver';

const APP = new URL('../src-tauri/target/release/switchboard', import.meta.url).pathname;
const SLOW = Number(process.env.SLOW ?? 1500);
// a run that crashed can leave a WebDriver session holding the only slot
try { _x("pkill -f '[t]auri-driver'; pkill -f '[W]ebKitWebDriver'"); } catch {} // stale
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('CLAUDE')));
const sh = (c) => { try { return execSync(c, { encoding: 'utf8' }).trim(); } catch (e) { return (e.stdout || '').trim(); } };
const port = () => sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7878/v1/machine");
const embeddedProcs = () => sh("pgrep -fc 'release/switchboard-daemo[n] serve' || true");
const serviceActive = () => sh('systemctl --user is-active switchboard-daemon');
let ok = true;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}  ${detail}`); ok &&= cond; };

async function openApp() {
  const proc = spawn(`${process.env.HOME}/.cargo/bin/tauri-driver`, [], { env, stdio: 'ignore' });
  await sleep(1500);
  const d = await new Builder().withCapabilities({ browserName: 'wry', 'tauri:options': { application: APP } })
    .usingServer('http://127.0.0.1:4444/').build();
  const x = (xp, t = 30000) => d.wait(until.elementLocated(By.xpath(xp)), t);
  await x("//span[contains(@class,'chip') and contains(., 'online')]");
  return { d, x, close: async () => { await d.quit().catch(() => {}); proc.kill(); await sleep(2500); } };
}
const settingsText = async (x) => (await x("//div[@role='dialog' and @aria-label='Settings']")).getText();

try {
  console.log('· opening the app (no service yet: it runs its own daemon)');
  let app = await openApp();
  check('app runs its own daemon', Number(embeddedProcs()) > 0, `embedded procs: ${embeddedProcs()}`);
  await sleep(SLOW);
  await (await app.x("//button[@aria-label='Settings']")).click();
  await sleep(SLOW);
  check('settings says inside the app', /running inside the app/.test(await settingsText(app.x)));
  console.log('· turning on "Keep agents running when Switchboard is closed"');
  await (await app.x("//label[contains(@class,'toggle')]//input")).click();
  await app.x("//*[contains(., 'Background service: running')]", 30000);
  await sleep(SLOW);
  writeFileSync('e2e/.results/real-service-on.png', await app.d.takeScreenshot(), 'base64');
  check('systemd service active', serviceActive() === 'active');
  check('app daemon handed over', embeddedProcs() === '0', `embedded procs: ${embeddedProcs()}`);
  check('tree still connected after the switch', /online/.test(await (await app.x("//header")).getText()));
  console.log('· closing the app');
  await app.close();
  check('daemon still answers with the app closed', port() === '401', `GET /v1/machine -> ${port()}`);
  console.log('· reopening the app (it should use the service, not start its own)');
  app = await openApp();
  await sleep(SLOW);
  check('reopened app uses the service', embeddedProcs() === '0');
  await (await app.x("//button[@aria-label='Settings']")).click();
  await sleep(SLOW);
  console.log('· turning the background service off again');
  await (await app.x("//label[contains(@class,'toggle')]//input")).click();
  await app.x("//*[contains(., 'running inside the app')]", 30000);
  await sleep(SLOW);
  check('service removed', serviceActive() !== 'active' && !sh('ls ~/.config/systemd/user/switchboard-daemon.service 2>/dev/null'));
  check('app runs its own daemon again', Number(embeddedProcs()) > 0);
  await app.close();
  check('nothing left running after close', port() === '000', `GET /v1/machine -> ${port()}`);
} catch (e) {
  console.error('FAILED:', e.message);
  ok = false;
}
process.exit(ok ? 0 : 1);
