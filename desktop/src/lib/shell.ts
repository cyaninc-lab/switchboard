// The only Tauri-specific code in the UI: ask the desktop shell for this machine's daemon.
// In a plain browser (tests, dev) there is no shell and this returns null.
import * as api from './api';
import type { Machine } from './api';

export const inShell = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

/** Pair with this machine's daemon (the app's own or a background service) without a code prompt. */
export async function pairLocal(): Promise<Machine | null> {
  if (!inShell()) return null;
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
export const serviceStatus = () => (inShell() ? call<ServiceStatus>('service_status') : Promise.resolve(null));
export const serviceInstall = () => call<void>('service_install');
export const serviceUninstall = () => call<void>('service_uninstall');

/** Open a path from the chat on this machine: folders and harmless files open, anything else is shown in its folder
 *  (the rule lives in the desktop app, lib.rs open_local). Returns "opened" or "revealed". */
export const openLocal = (path: string) => import('@tauri-apps/api/core').then(({ invoke }) => invoke<string>('open_local', { path }));

/** Open a URL in the user's browser (the system one in the desktop app). */
export async function openExternal(url: string) {
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
  if (!inShell()) return null;
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
export async function notify(title: string, body: string) {
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
