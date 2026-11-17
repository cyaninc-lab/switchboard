// Daemon client: the only place that talks to daemons (protocol: docs/protocol.md).
// Plain fetch, no Tauri APIs, so the same code can serve a browser or mobile build.

export type Machine = { url: string; token: string; name: string };
export type Session = {
  session_id: string; summary: string; cwd: string | null; git_branch: string | null;
  last_modified: number; custom_title: string | null; entry_id: string | null;
};
export type Entry = {
  id: string; session_id: string; name: string; group: string; pinned: boolean;
  account: string | null; mode: string | null; model: string | null; effort?: string | null;
  usage?: Usage | null;
  state: 'new' | 'working' | 'waiting' | 'ready' | 'stopped' | 'terminal' | 'missing' | 'error';
  session: Session | null;
};
export type HistoryMessage = { type: 'user' | 'assistant'; uuid: string; message: any; parent_tool_use_id?: string | null };

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
export type WatchKind = 'schedule' | 'command' | 'http' | 'github';
export type Watch = {
  id: string; name: string; entry_id: string | null; kind: WatchKind; config: Record<string, any>;
  action: 'notify' | 'wake'; instructions: string | null; mode: string | null; enabled: boolean; last_fired: number | null;
};
export type InboxItem = {
  id: number; created: number; source: string; title: string; body: string; watch_id: string | null;
  entry_id: string | null; from_agent: boolean; read: boolean;
};
export const watches = (m: Machine) => call<{ watches: Watch[] }>(m, '/v1/watches').then((d) => d.watches);
export const saveWatch = (m: Machine, w: Partial<Watch>) => w.id
  ? call<Watch>(m, `/v1/watches/${w.id}`, { method: 'PATCH', body: JSON.stringify(w) })
  : call<Watch>(m, '/v1/watches', { method: 'POST', body: JSON.stringify(w) });
export const removeWatch = (m: Machine, id: string) => call<void>(m, `/v1/watches/${id}`, { method: 'DELETE' });
export const testWatch = (m: Machine, id: string) => call<{ fired: boolean }>(m, `/v1/watches/${id}/test`, { method: 'POST' });
export const inbox = (m: Machine) => call<{ items: InboxItem[] }>(m, '/v1/inbox').then((d) => d.items);
export const markRead = (m: Machine, id?: number) => call<void>(m, id === undefined ? '/v1/inbox/read' : `/v1/inbox/${id}/read`, { method: 'POST' });

export type Account = { name: string; kind: 'oauth' | 'api_key'; created: number };
export const accounts = (m: Machine) => call<{ accounts: Account[] }>(m, '/v1/accounts').then((d) => d.accounts);
export const addAccount = (m: Machine, name: string, token: string, kind: 'oauth' | 'api_key' = 'oauth') =>
  call<{ accounts: Account[] }>(m, '/v1/accounts', { method: 'POST', body: JSON.stringify({ name, token, kind }) }).then((d) => d.accounts);
export const removeAccount = (m: Machine, name: string) => call<void>(m, `/v1/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' });
export type Network = { listen_tailscale: boolean; active: string[]; error: string | null; port: number;
  url: string | null; tailscale: { name: string | null; dns: string | null; ips: string[] } | null };
export const network = (m: Machine) => call<Network>(m, '/v1/network');
export const setNetwork = (m: Machine, on: boolean) => call<Network>(m, '/v1/network', { method: 'PUT', body: JSON.stringify({ listen_tailscale: on }) });
export const pairCode = (m: Machine) => call<{ code: string; expires_in: number }>(m, '/v1/pair_code', { method: 'POST' });
export const isLocal = (m: Machine) => /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(m.url);
export type Usage = { context_used: number; context_window: number | null; turn_cost: number | null; session_cost: number };
export type Model = { value: string; displayName: string; description?: string; supportsEffort?: boolean; supportedEffortLevels?: string[] };
export const models = (m: Machine) => call<{ models: Model[] }>(m, '/v1/models').then((d) => d.models);
export const addEntry = (m: Machine, body: { session_id?: string; new?: { cwd: string }; name?: string; group?: string; mode?: string; model?: string; account?: string }) =>
  call<Entry>(m, '/v1/entries', { method: 'POST', body: JSON.stringify(body) });
export const updateEntry = (m: Machine, id: string, body: Partial<{ name: string; group: string; pinned: boolean }>) =>
  call<Entry>(m, `/v1/entries/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
export const removeEntry = (m: Machine, id: string) => call<void>(m, `/v1/entries/${id}`, { method: 'DELETE' });
export const folders = (m: Machine) => call<{ home: string; folders: string[] }>(m, '/v1/folders');
export const commands = (m: Machine) => call<{ commands: Command[] }>(m, '/v1/commands').then((d) => d.commands);
export const history = (m: Machine, id: string, limit = 400) =>
  call<{ messages: HistoryMessage[] }>(m, `/v1/entries/${id}/messages?limit=${limit}`).then((d) => d.messages);

/** Flatten an SDK message into display lines: text, tool calls and tool results. */
export type DiffLine = { t: '+' | '-' | ' '; s: string };
export type Line =
  | { kind: 'text' | 'result' | 'image' | 'thinking'; text: string }
  | { kind: 'tool'; text: string; name: string; id: string; input: any }
  | { kind: 'diff'; text: string; file: string; lines: DiffLine[]; note?: string }
  | { kind: 'todos'; text: string; items: { content: string; status: string; activeForm?: string }[] };

/** Line diff by longest common subsequence; big inputs fall back to all-removed / all-added. */
export function lineDiff(a: string, b: string): DiffLine[] {
  const A = a.split('\n'), B = b.split('\n');
  if (A.length * B.length > 250_000) return [...A.map((s) => ({ t: '-' as const, s })), ...B.map((s) => ({ t: '+' as const, s }))];
  const L = Array.from({ length: A.length + 1 }, () => new Int32Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--)
    L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) { out.push({ t: ' ', s: A[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ t: '-', s: A[i++] });
    else out.push({ t: '+', s: B[j++] });
  }
  while (i < A.length) out.push({ t: '-', s: A[i++] });
  while (j < B.length) out.push({ t: '+', s: B[j++] });
  return out;
}
export type Image = { media_type: string; data: string };
export type Command = { name: string; description: string };
// Claude Code stores slash commands and their output wrapped in tags; show them as the user typed / saw them.
function untag(t: string): string {
  const name = t.match(/<command-name>\s*([^<]*?)\s*<\/command-name>/)?.[1];
  if (name) {
    const args = t.match(/<command-args>([\s\S]*?)<\/command-args>/)?.[1]?.trim();
    return `${name.startsWith('/') ? name : '/' + name}${args ? ' ' + args : ''}`;
  }
  return t.replace(/<\/?local-command-std(out|err)>/g, '').replace(/<command-message>[\s\S]*?<\/command-message>/g, '');
}

export function render(msg: HistoryMessage): Line[] {
  const c = msg.message?.content;
  if (typeof c === 'string') return [{ kind: 'text', text: untag(c) }];
  if (!Array.isArray(c)) return [];
  return c.flatMap((b: any): Line[] => {
    if (b.type === 'text') return [{ kind: 'text' as const, text: untag(b.text) }];
    if (b.type === 'thinking' && b.thinking) return [{ kind: 'thinking' as const, text: b.thinking }];
    if (b.type === 'image' && b.source?.type === 'base64') return [{ kind: 'image' as const, text: `data:${b.source.media_type};base64,${b.source.data}` }];
    if (b.type === 'tool_use') {
      const i = b.input ?? {};
      if (b.name === 'Edit' && typeof i.old_string === 'string')
        return [{ kind: 'diff', text: `Edit(${i.file_path})`, file: i.file_path, lines: lineDiff(i.old_string, i.new_string ?? ''), note: i.replace_all ? 'all occurrences' : undefined }];
      if (b.name === 'MultiEdit' && Array.isArray(i.edits))
        return i.edits.map((e: any, n: number) => ({ kind: 'diff' as const, text: `Edit(${i.file_path})`, file: i.file_path, lines: lineDiff(e.old_string ?? '', e.new_string ?? ''), note: `edit ${n + 1} of ${i.edits.length}` }));
      if (b.name === 'Write' && typeof i.content === 'string')
        return [{ kind: 'diff', text: `Write(${i.file_path})`, file: i.file_path, lines: i.content.split('\n').map((s: string) => ({ t: '+' as const, s })), note: 'new contents' }];
      if (b.name === 'TodoWrite' && Array.isArray(i.todos))
        return [{ kind: 'todos', text: 'To-dos', items: i.todos }];
      const arg = i.command ?? i.file_path ?? i.pattern ?? i.description ?? '';
      return [{ kind: 'tool' as const, text: `${b.name}(${String(arg).slice(0, 160)})`, name: b.name, id: b.id, input: i }];
    }
    if (b.type === 'tool_result') {
      const t = Array.isArray(b.content) ? b.content.map((x: any) => x.text ?? '').join('\n') : String(b.content ?? '');
      if (/^Todos have been modified successfully/.test(t)) return [];  // the checklist above already shows it
      return [{ kind: 'result' as const, text: t }];
    }
    return [];
  });
}

// ---- live connection (phase 2): one WebSocket per machine ----
export type LiveEvent =
  | { type: 'hello'; machine: string }
  | { type: 'state'; entry: string; state: Entry['state'] | 'ready' }
  | { type: 'delta'; entry: string; text: string; kind?: 'thinking' }
  | { type: 'message'; entry: string; message: HistoryMessage }
  | { type: 'permission_request'; entry: string; request_id: string; tool: string; kind: 'tool' | 'question' | 'plan'; input: any; remember: string[] }
  | { type: 'permission_resolved'; entry: string; request_id: string; allow: boolean; by: string }
  | { type: 'mode'; entry: string; mode: string }
  | { type: 'model'; entry: string; model: string | null }
  | { type: 'effort'; entry: string; effort: string | null }
  | { type: 'account'; entry: string; account: string | null }
  | { type: 'terminal_opened' | 'terminal_closed'; entry: string }
  | { type: 'commands'; entry: string; commands: string[] }
  | { type: 'notice'; entry: string; text: string }
  | { type: 'queued'; entry: string; count: number }
  | ({ type: 'usage'; entry: string } & Usage)
  | { type: 'inbox'; item: InboxItem }
  | { type: 'inbox_read'; id: number | null }
  | { type: 'terminal_output'; entry: string; data: string }
  | { type: 'error'; entry?: string; code: string; message: string };

export const MODES = ['default', 'acceptEdits', 'plan', 'auto', 'dontAsk', 'bypassPermissions'] as const;

export class Live {
  private ws: WebSocket | null = null;
  private closed = false;
  constructor(private m: Machine, private onEvent: (e: LiveEvent) => void, private onStatus: (up: boolean) => void) {
    this.open();
  }
  private open() {
    const ws = new WebSocket(this.m.url.replace(/^http/, 'ws').replace(/\/$/, '') + '/v1/ws');
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: this.m.token }));
    ws.onmessage = (ev) => {
      const e = JSON.parse(ev.data) as LiveEvent;
      if (e.type === 'hello') this.onStatus(true);
      this.onEvent(e);
    };
    ws.onclose = () => {
      this.onStatus(false);
      if (!this.closed) setTimeout(() => this.open(), 2000); // reconnect: the daemon may restart
    };
  }
  private sendJson(o: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(o));
    else throw new Error('Not connected to ' + this.m.name);
  }
  send(entry: string, text: string, images: Image[] = []) { this.sendJson({ type: 'send', entry, text, images }); }
  interrupt(entry: string) { this.sendJson({ type: 'interrupt', entry }); }
  stop(entry: string) { this.sendJson({ type: 'stop', entry }); }
  setMode(entry: string, mode: string) { this.sendJson({ type: 'set_mode', entry, mode }); }
  setModel(entry: string, model: string | null) { this.sendJson({ type: 'set_model', entry, model }); }
  setEffort(entry: string, effort: string | null) { this.sendJson({ type: 'set_effort', entry, effort }); }
  setAccount(entry: string, account: string | null) { this.sendJson({ type: 'set_account', entry, account }); }
  answer(request_id: string, allow: boolean, extra: { message?: string; remember?: string; answers?: Record<string, string | string[]>; response?: string } = {}) {
    this.sendJson({ type: 'permission_reply', request_id, allow, ...extra });
  }
  terminalOpen(entry: string, cols: number, rows: number) { this.sendJson({ type: 'terminal_open', entry, cols, rows }); }
  terminalInput(entry: string, data: string) { this.sendJson({ type: 'terminal_input', entry, data }); }
  terminalResize(entry: string, cols: number, rows: number) { this.sendJson({ type: 'terminal_resize', entry, cols, rows }); }
  terminalClose(entry: string) { this.sendJson({ type: 'terminal_close', entry }); }
  close() { this.closed = true; this.ws?.close(); }
}
