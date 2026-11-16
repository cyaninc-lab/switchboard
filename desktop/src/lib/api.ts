// Daemon client: the only place that talks to daemons (protocol: docs/protocol.md).
// Plain fetch, no Tauri APIs, so the same code can serve a browser or mobile build.

export type Machine = { url: string; token: string; name: string };
export type Session = {
  session_id: string; summary: string; cwd: string | null; git_branch: string | null;
  last_modified: number; custom_title: string | null; entry_id: string | null;
};
export type Entry = {
  id: string; session_id: string; name: string; group: string; pinned: boolean;
  account: string | null; mode: string | null;
  state: 'working' | 'waiting' | 'stopped' | 'terminal' | 'missing' | 'error';
  session: Session | null;
};
export type HistoryMessage = { type: 'user' | 'assistant'; uuid: string; message: any };

const KEY = 'switchboard.machines';

// ponytail: tokens in localStorage for phase 1; move to the OS keychain before this leaves the dev machine
export function loadMachines(): Machine[] {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; }
}
export function saveMachines(ms: Machine[]) {
  localStorage.setItem(KEY, JSON.stringify(ms));
}

async function call<T>(m: Machine, path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(m.url.replace(/\/$/, '') + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${m.token}`, ...(init.headers || {}) },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.code || `${r.status} ${r.statusText}`);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

export async function pair(url: string, code: string, clientName: string): Promise<Machine> {
  const r = await fetch(url.replace(/\/$/, '') + '/v1/pair', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, client_name: clientName }),
  });
  if (!r.ok) throw new Error(r.status === 403 ? 'That code is wrong or expired' : `${r.status} ${r.statusText}`);
  const d = await r.json();
  return { url, token: d.token, name: d.machine };
}

export const entries = (m: Machine) => call<{ entries: Entry[] }>(m, '/v1/entries').then((d) => d.entries);
export const sessions = (m: Machine, q = '', limit = 50) =>
  call<{ total: number; sessions: Session[] }>(m, `/v1/sessions?q=${encodeURIComponent(q)}&limit=${limit}`);
export const addEntry = (m: Machine, body: { session_id: string; name?: string; group?: string }) =>
  call<Entry>(m, '/v1/entries', { method: 'POST', body: JSON.stringify(body) });
export const updateEntry = (m: Machine, id: string, body: Partial<{ name: string; group: string; pinned: boolean }>) =>
  call<Entry>(m, `/v1/entries/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
export const removeEntry = (m: Machine, id: string) => call<void>(m, `/v1/entries/${id}`, { method: 'DELETE' });
export const history = (m: Machine, id: string, limit = 400) =>
  call<{ messages: HistoryMessage[] }>(m, `/v1/entries/${id}/messages?limit=${limit}`).then((d) => d.messages);

/** Flatten an SDK message into display lines: text, tool calls and tool results. */
export type Line = { kind: 'text' | 'tool' | 'result'; text: string };
export function render(msg: HistoryMessage): Line[] {
  const c = msg.message?.content;
  if (typeof c === 'string') return [{ kind: 'text', text: c }];
  if (!Array.isArray(c)) return [];
  return c.flatMap((b: any): Line[] => {
    if (b.type === 'text') return [{ kind: 'text' as const, text: b.text }];
    if (b.type === 'tool_use') {
      const arg = b.input?.command ?? b.input?.file_path ?? b.input?.pattern ?? b.input?.description ?? '';
      return [{ kind: 'tool' as const, text: `${b.name}(${String(arg).slice(0, 160)})` }];
    }
    if (b.type === 'tool_result') {
      const t = Array.isArray(b.content) ? b.content.map((x: any) => x.text ?? '').join('\n') : String(b.content ?? '');
      return [{ kind: 'result' as const, text: t.split('\n').slice(0, 6).join('\n') }];
    }
    return [];
  });
}
