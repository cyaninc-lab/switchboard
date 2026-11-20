// The only Tauri-specific code in the UI: ask the desktop shell for this machine's daemon.
// In a plain browser (tests, dev) there is no shell and this returns null.
import * as api from './api';
import type { Machine } from './api';

export const inShell = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
/** The phone app (Tauri on Android): no daemon of its own, no files, no windows, no self-update: it connects to machines. */
export const isPhoneApp = () => inShell() && /Android|iPhone|iPad/i.test(navigator.userAgent);
/** The desktop app (Tauri on Linux/Windows): its own daemon, background service, updater, windows. */
export const isDesktopApp = () => inShell() && !isPhoneApp();
// the split pane is an iframe, which the desktop app doesn't reach into: it asks the main window (its parent) to open
// links and files and to notify (the parent says so on load; it only accepts these from its own pane)
let viaParent = false;
export const useParentShell = () => { viaParent = true; };
export const canOpenLocally = () => isDesktopApp() || viaParent;  // the phone app has no machine files to open
const askParent = (msg: Record<string, unknown>) => window.parent.postMessage({ sb: true, ...msg }, location.origin);

/** Pair with this machine's daemon (the app's own or a background service) without a code prompt. */
export async function pairLocal(): Promise<Machine | null> {
  if (!isDesktopApp()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  const d = await invoke<{ url: string; code: string; embedded: boolean }>('local_daemon');
  return api.pair(d.url, d.code, 'Switchboard desktop (this machine)');
}

export type ServiceStatus = { supported: boolean; installed: boolean; active: boolean; embedded: boolean };

async function call<T>(cmd: string): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd);
}
/** Background service: keeps agents running while the app is closed (null outside the desktop app). */
export const serviceStatus = () => (isDesktopApp() ? call<ServiceStatus>('service_status') : Promise.resolve(null));
export const serviceInstall = () => call<void>('service_install');
export const serviceUninstall = () => call<void>('service_uninstall');

/** Open a path from the chat on this machine: folders and harmless files open, anything else is shown in its folder
 *  (the rule lives in the desktop app, lib.rs open_local). Returns "opened" or "revealed". */
export const openLocal = (path: string): Promise<string> =>
  viaParent && !inShell() ? (askParent({ do: 'path', path }), Promise.resolve('asked'))
    : import('@tauri-apps/api/core').then(({ invoke }) => invoke<string>('open_local', { path }));

/** Open a URL in the user's browser (the system one in the desktop app). */
export async function openExternal(url: string) {
  if (viaParent && !inShell()) return askParent({ do: 'url', url });
  if (inShell()) {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl(url);
  } else {
    window.open(url, '_blank', 'noopener');
  }
}
export async function appVersion() {
  if (!inShell()) return 'dev (browser)';
  const { getVersion } = await import('@tauri-apps/api/app');
  return getVersion();
}

// ---- self-update (signed, from cyaninc-lab/switchboard's releases) ----
export type Update = { version: string; notes: string; install: (onProgress: (pct: number) => void) => Promise<void> };

/** null when up to date (or outside the desktop app). Throws if the check itself fails. */
export async function checkForUpdate(): Promise<Update | null> {
  if (!isDesktopApp()) return null;  // the phone app updates through its APK (later: its own check)
  const { check } = await import('@tauri-apps/plugin-updater');
  const u = await check();
  if (!u) return null;
  return {
    version: u.version,
    notes: u.body ?? '',
    install: async (onProgress) => {
      let total = 0, done = 0;
      await u.downloadAndInstall((ev) => {
        if (ev.event === 'Started') total = ev.data.contentLength ?? 0;
        if (ev.event === 'Progress') { done += ev.data.chunkLength; if (total) onProgress(Math.round((done / total) * 100)); }
      });
      const { relaunch } = await import('@tauri-apps/plugin-process');
      await relaunch();
    },
  };
}

/** A desktop notification (the system's own in the desktop app; the browser's if allowed; else nothing). */
let phonePushOn = false;  // the phone app with notifications set up: PushService.kt shows them, not the web UI (no doubles)
export async function notify(title: string, body: string) {
  if (phonePushOn) return;
  if (viaParent && !inShell()) return askParent({ do: 'notify', title, body });
  try {
    if (inShell()) {
      const n = await import('@tauri-apps/plugin-notification');
      if (!(await n.isPermissionGranted()) && (await n.requestPermission()) !== 'granted') return;
      n.sendNotification({ title, body: body.slice(0, 240) });
    } else if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      new Notification(title, { body: body.slice(0, 240) });
    }
  } catch (e) { console.warn('notification failed', e); }
}

// ---- pop-out windows: one session in its own window. A session is open in one place only; the main window owns the
// tree, inbox, unread dots and notifications, a popped-out window is just that session's chat. ----
export type Handoff = { url: string; entry: string; text: string; images: unknown[] };
const HANDOFF = 'switchboard.handoff.';
export const windowLabel = (entry: string) => 's-' + entry.replace(/[^a-zA-Z0-9_-]/g, '_');
/** The session this window shows, when it's a popped-out window (null in the main window). */
export const soloOf = () => {
  if (typeof location === 'undefined') return null;
  const p = new URLSearchParams(location.search);
  // embed: shown inside the main window's split pane (an iframe), not a window of its own
  return p.get('solo') && p.get('machine') ? { url: p.get('machine')!, entry: p.get('solo')!, embed: p.get('embed') === '1' } : null;
};
/** Open a session in its own window (or bring its window to the front), carrying its draft along. */
export async function popOut(h: Handoff, title: string) {
  const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');
  const label = windowLabel(h.entry);
  const have = await WebviewWindow.getByLabel(label);
  if (have) return void (await have.setFocus());
  try { localStorage.setItem(HANDOFF + h.entry, JSON.stringify(h)); } catch {}
  const w = new WebviewWindow(label, { url: `/?machine=${encodeURIComponent(h.url)}&solo=${encodeURIComponent(h.entry)}`, title: `${title} · Switchboard`,
    width: 760, height: 820, minWidth: 420, minHeight: 360,
    dragDropEnabled: false });  // as the main window (tauri.conf.json): Tauri's own file drop would block HTML drag and drop on Windows
  await new Promise<void>((ok, fail) => { w.once('tauri://created', () => ok()); w.once('tauri://error', (e) => fail(new Error(String(e.payload)))); });
}
/** In a popped-out window: the draft the main window handed over (once). */
export function takeHandoff(entry: string): Handoff | null {
  try { const v = localStorage.getItem(HANDOFF + entry); localStorage.removeItem(HANDOFF + entry); return v ? JSON.parse(v) : null; } catch { return null; }
}
export async function focusWindow(entry: string) {
  const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');
  const w = await WebviewWindow.getByLabel(windowLabel(entry));
  if (w) { await w.unminimize(); await w.setFocus(); return true; }
  return false;
}
/** In a popped-out window: hand the session (with its draft) back to the main window as it closes. */
export async function onSoloClosing(get: () => Handoff) {
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  const { emitTo } = await import('@tauri-apps/api/event');
  const w = getCurrentWindow();
  await w.onCloseRequested(async () => { await emitTo('main', 'solo-closed', get()); });
}
/** In the main window: popped-out windows opening and closing. */
export async function onSoloEvents(opened: (entry: string) => void, closed: (h: Handoff) => void) {
  const { listen } = await import('@tauri-apps/api/event');
  await listen<string>('solo-opened', (e) => opened(e.payload));
  await listen<Handoff>('solo-closed', (e) => closed(e.payload));
}
export async function soloOpened(entry: string) {
  const { emitTo } = await import('@tauri-apps/api/event');
  await emitTo('main', 'solo-opened', entry);
}
export async function closeThisWindow() {
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  await getCurrentWindow().close();
}
export async function setOnTop(on: boolean) {
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  await getCurrentWindow().setAlwaysOnTop(on);
}

// ---- phone notifications (the phone app): PushService.kt listens on ntfy with what push_config hands it ----
export type PhonePush = { server: string; topic: string; machines: Machine[]; details: boolean; finished: boolean };
export async function setPhonePush(cfg: PhonePush) {
  const { invoke } = await import('@tauri-apps/api/core');
  const n = await import('@tauri-apps/plugin-notification');
  if (!(await n.isPermissionGranted())) await n.requestPermission();  // Android 13+: notifications need a yes
  await invoke('push_config', { config: JSON.stringify(cfg) });
  phonePushOn = cfg.machines.length > 0;
}
/** The session a tapped notification named (once), or null. */
export async function takeNotificationOpen(): Promise<{ machine: string; entry: string } | null> {
  const { invoke } = await import('@tauri-apps/api/core');
  const s = await invoke<string | null>('push_take_open');
  return s ? JSON.parse(s) : null;
}

// ---- pairing by QR code: the machine's own address with the code, http://<machine>:7878/?pair=<code>. Any camera
// can scan it (the browser opens the web app, which pairs at once); the phone app reads the same address ----
export const pairLink = (url: string, code: string) => `${url.replace(/\/$/, '')}/?pair=${encodeURIComponent(code)}`;
export function parsePairLink(text: string): { url: string; code: string } | null {
  try {
    const u = new URL(text.trim());
    const code = u.searchParams.get('pair');
    return /^https?:$/.test(u.protocol) && code ? { url: u.origin, code } : null;
  } catch { return null; }
}
/** The phone app: scan a pairing QR code with the camera (asks for camera permission the first time). */
export async function scanPairCode(): Promise<{ url: string; code: string } | null> {
  const { scan, Format, checkPermissions, requestPermissions } = await import('@tauri-apps/plugin-barcode-scanner');
  if ((await checkPermissions()) !== 'granted' && (await requestPermissions()) !== 'granted') throw new Error('camera permission denied');
  const r = await scan({ formats: [Format.QRCode], windowed: false });
  return parsePairLink(r.content);
}
