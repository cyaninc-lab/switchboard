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
