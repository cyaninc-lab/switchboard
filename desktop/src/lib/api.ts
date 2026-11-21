// Daemon client: the only place that talks to daemons (protocol: docs/protocol.md).
// Plain fetch, no Tauri APIs, so the same code can serve a browser or mobile build.

export type Machine = { url: string; token: string; name: string };
export type Session = {
  session_id: string; summary: string; cwd: string | null; git_branch: string | null;
  last_modified: number; custom_title: string | null; entry_id: string | null;
};
// a background task Claude Code reports (Bash run_in_background, a background agent)
export type Task = { id: string; status: 'running' | 'completed' | 'failed' | 'stopped'; started: number; ended?: number; description?: string;
  type?: string; tool_use_id?: string; summary?: string; output_file?: string; last_tool_name?: string };
export type Entry = {
  id: string; session_id: string; name: string; group: string; pinned: boolean;
  account: string | null; mode: string | null; model: string | null; effort?: string | null;
  usage?: Usage | null;
  state: 'new' | 'working' | 'waiting' | 'ready' | 'stopped' | 'terminal' | 'missing' | 'error';
  session: Session | null;
  subagents?: Sub[];
  tasks?: Task[];
};
/** A subagent of a session; parent is the subagent that spawned it (null: the session itself did). */
export type Sub = { id: string; description: string; type: string | null; parent: string | null; tool_use_id: string | null; finished: boolean; stopped: boolean; updated: number; running?: boolean };
// patches: an edit's hunks by tool_use id (Claude Code's structuredPatch), for line numbers in its diff
/** A unique id for a message shown before the daemon echoes it. crypto.randomUUID only exists on secure pages
 *  (https, localhost): a phone opening http://<machine>:7878 has none, and sending then failed after the send. */
export const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
/** Copy text: the clipboard API is missing on plain-http pages (a phone on http://<machine>:7878), so fall back to
 *  the old select-and-copy way there. */
export async function copy(text: string) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const t = Object.assign(document.createElement('textarea'), { value: text });
  t.style.cssText = 'position:fixed; opacity:0';
  document.body.append(t); t.select();
  const ok = document.execCommand('copy');
  t.remove();
  if (!ok) throw new Error('copy failed');
}
export type Hunk = { oldStart: number; oldLines: number; newStart: number; newLines: number; lines: string[] };
export type HistoryMessage = { type: 'user' | 'assistant'; uuid: string; message: any; parent_tool_use_id?: string | null; patches?: Record<string, Hunk[]> };

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

// 10 s: a machine that can't be reached (off, or this device off its Tailscale) otherwise hangs for minutes, looking empty
export const entries = (m: Machine) => call<{ entries: Entry[] }>(m, '/v1/entries', { signal: AbortSignal.timeout(10_000) }).then((d) => d.entries);
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
export const deleteInboxItem = (m: Machine, id: number) => call<void>(m, `/v1/inbox/${id}`, { method: 'DELETE' });
export const markRead = (m: Machine, id?: number) => call<void>(m, id === undefined ? '/v1/inbox/read' : `/v1/inbox/${id}/read`, { method: 'POST' });

export type Account = { name: string; kind: 'oauth' | 'api_key'; created: number };
export const accounts = (m: Machine) => call<{ accounts: Account[] }>(m, '/v1/accounts').then((d) => d.accounts);
export const addAccount = (m: Machine, name: string, token: string, kind: 'oauth' | 'api_key' = 'oauth') =>
  call<{ accounts: Account[] }>(m, '/v1/accounts', { method: 'POST', body: JSON.stringify({ name, token, kind }) }).then((d) => d.accounts);
export const removeAccount = (m: Machine, name: string) => call<void>(m, `/v1/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' });
export type Network = { listen_tailscale: boolean; active: string[]; error: string | null; port: number;
  url: string | null; tailscale: { name: string | null; dns: string | null; ips: string[] } | null };
export const network = (m: Machine) => call<Network>(m, '/v1/network');
// cross-machine messages: a token from `m` for another machine's daemon; `m`'s list of machines its sessions may message
export const peerToken = (m: Machine, machine: string) => call<{ token: string }>(m, '/v1/peer_token', { method: 'POST', body: JSON.stringify({ machine }) });
export const setPeers = (m: Machine, peers: { name: string; url: string; token: string }[]) =>
  call<{ peers: { name: string; url: string }[] }>(m, '/v1/peers', { method: 'PUT', body: JSON.stringify({ peers }) });
export const files = (m: Machine, entry: string, q: string) => call<{ files: string[] }>(m, `/v1/entries/${entry}/files?q=${encodeURIComponent(q)}`);
// bug reports to GitHub through this machine's daemon (GitHub's sign-in can't be called from a page)
export type GitHub = { signed_in: boolean; login: string | null };
export const githubStatus = (m: Machine) => call<GitHub>(m, '/v1/github');
export const githubStart = (m: Machine) => call<{ device_code: string; user_code: string; verification_uri: string; expires_in: number; interval: number }>(m, '/v1/github/device', { method: 'POST' });
export const githubPoll = (m: Machine, device_code: string) => call<{ status: string; login?: string }>(m, '/v1/github/poll', { method: 'POST', body: JSON.stringify({ device_code }) });
export const githubSignOut = (m: Machine) => call<GitHub>(m, '/v1/github', { method: 'DELETE' });
export const githubIssue = (m: Machine, title: string, body: string) => call<{ url: string; number: number }>(m, '/v1/github/issue', { method: 'POST', body: JSON.stringify({ title, body }) });
export const machineInfo = (m: Machine) => call<{ name: string; daemon: string; sdk: string; os: string }>(m, '/v1/machine');
export type NewSessionDefaults = { mode?: string; model?: string; effort?: string; account?: string };
export type DaemonSettings = { idle_close_minutes: number; project_order?: string[]; new_session?: NewSessionDefaults; claude_default_mode?: string };
export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
export const settings = (m: Machine) => call<DaemonSettings>(m, '/v1/settings');
// phone notifications: the ntfy server (set on one machine is enough) and the phones subscribed to this daemon
export type PushInfo = { server: string; subscribers: number };
export const push = (m: Machine) => call<PushInfo>(m, '/v1/push');
export const setPush = (m: Machine, b: { server?: string; subscribe?: string; machine_url?: string; unsubscribe?: string }) =>
  call<PushInfo>(m, '/v1/push', { method: 'PUT', body: JSON.stringify(b) });
export const setSettings = (m: Machine, s: Partial<DaemonSettings>) => call<DaemonSettings>(m, '/v1/settings', { method: 'PUT', body: JSON.stringify(s) });
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
// sessions taken off the tree (kept with their name, project and settings), and putting one back
export const removedEntries = (m: Machine) => call<{ entries: (Entry & { removed: number })[] }>(m, '/v1/entries/removed').then((d) => d.entries);
export const restoreEntry = (m: Machine, id: string) => call<Entry>(m, `/v1/entries/${id}/restore`, { method: 'POST' });
// off the tree (the session stays on its machine), or forever: the session itself is deleted from its machine
export const removeEntry = (m: Machine, id: string, forever = false) => call<void>(m, `/v1/entries/${id}${forever ? '?forever=1' : ''}`, { method: 'DELETE' });
export const folders = (m: Machine) => call<{ home: string; folders: string[] }>(m, '/v1/folders');
export const commands = (m: Machine) => call<{ commands: Command[] }>(m, '/v1/commands').then((d) => d.commands);
export const subagentHistory = (m: Machine, id: string, agent: string, limit = 400) =>
  call<{ messages: HistoryMessage[] }>(m, `/v1/entries/${id}/subagents/${encodeURIComponent(agent)}/messages?limit=${limit}`).then((d) => d.messages);
export const history = (m: Machine, id: string, limit = 400) =>
  call<{ messages: HistoryMessage[]; patches?: Record<string, Hunk[]> }>(m, `/v1/entries/${id}/messages?limit=${limit}`).then((d) => {
    if (d.patches && Object.keys(d.patches).length) d.messages.push({ type: 'user', uuid: 'patches', message: { content: [] }, patches: d.patches });  // carried like live ones
    return d.messages;
  });
/** An edit's hunks as diff lines with line numbers (the new file's for kept and added lines, the old one's for removed). */
export function hunkLines(hunks: Hunk[]): DiffLine[] {
  const out: DiffLine[] = [];
  hunks.forEach((h, i) => {
    if (i) out.push({ t: '…', s: '' });
    let o = h.oldStart, n = h.newStart;
    for (const l of h.lines) {
      const t = l[0] === '+' || l[0] === '-' ? l[0] : ' ';
      out.push({ t, s: l.slice(1), n: t === '-' ? o : n });
      if (t !== '+') o++;
      if (t !== '-') n++;
    }
  });
  return out;
}

/** Flatten an SDK message into display lines: text, tool calls and tool results. */
export type DiffLine = { t: '+' | '-' | ' ' | '…'; s: string; n?: number };
export type Line =
  | { kind: 'text' | 'image' | 'thinking'; text: string }
  | { kind: 'result'; text: string; id?: string }  // id: the tool_use it answers (a Bash window shows its command)
  | { kind: 'tool'; text: string; name: string; id: string; input: any }
  | { kind: 'diff'; text: string; file: string; lines: DiffLine[]; note?: string; id?: string }
  | { kind: 'todos'; text: string; items: { content: string; status: string; activeForm?: string }[] }
  | { kind: 'mail'; text: string; dir: 'in' | 'out'; who: string };  // a message between sessions (message_session)

// how the daemon delivers another session's message (runner._tools); parsed so history renders it too
const MAIL_IN = /^Message from the session "([^"]*)" \(sent through Switchboard[^\n]*\):\n\n([\s\S]*)$/;
// Claude Code tells the session a background task ended with a "user" message holding <task-notification>: it's
// Claude Code talking, not the user, so it shows as a step on Claude's side ("Background command … completed")
const TASK_NOTE = /^\s*<task-notification>[\s\S]*?<summary>([\s\S]*?)<\/summary>[\s\S]*<\/task-notification>\s*$/;
const userText = (msg: HistoryMessage, t: string): Line => {
  const note = msg.type === 'user' ? TASK_NOTE.exec(t) : null;
  if (note) return { kind: 'tool', text: note[1].trim(), name: 'TaskNotification', id: '', input: {} };
  const m = msg.type === 'user' ? MAIL_IN.exec(t) : null;
  return m ? { kind: 'mail', dir: 'in', who: m[1], text: m[2] } : { kind: 'text', text: t };
};

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
  if (typeof c === 'string') return [userText(msg, untag(c))];
  if (!Array.isArray(c)) return [];
  return c.flatMap((b: any): Line[] => {
    if (b.type === 'text') return [userText(msg, untag(b.text))];
    if (b.type === 'thinking' && b.thinking) return [{ kind: 'thinking' as const, text: b.thinking }];
    if (b.type === 'image' && b.source?.type === 'base64') return [{ kind: 'image' as const, text: `data:${b.source.media_type};base64,${b.source.data}` }];
    if (b.type === 'tool_use') {
      const i = b.input ?? {};
      if (b.name === 'Edit' && typeof i.old_string === 'string')
        return [{ kind: 'diff', text: `Edit(${i.file_path})`, file: i.file_path, lines: lineDiff(i.old_string, i.new_string ?? ''), note: i.replace_all ? 'all occurrences' : undefined, id: b.id }];
      if (b.name === 'MultiEdit' && Array.isArray(i.edits))
        return i.edits.map((e: any, n: number) => ({ kind: 'diff' as const, text: `Edit(${i.file_path})`, file: i.file_path, lines: lineDiff(e.old_string ?? '', e.new_string ?? ''), note: `edit ${n + 1} of ${i.edits.length}` }));
      if (b.name === 'Write' && typeof i.content === 'string')
        return [{ kind: 'diff', text: `Write(${i.file_path})`, file: i.file_path, lines: i.content.split('\n').map((s: string) => ({ t: '+' as const, s })), note: 'new contents', id: b.id }];
      if (b.name === 'mcp__switchboard__message_session' && typeof i.text === 'string')
        return [{ kind: 'mail', dir: 'out', who: String(i.session ?? ''), text: i.text }];
      if (b.name === 'TodoWrite' && Array.isArray(i.todos))
        return [{ kind: 'todos', text: 'To-dos', items: i.todos }];
      const arg = i.command ?? i.file_path ?? i.pattern ?? i.description ?? i.questions?.[0]?.question ?? '';  // AskUserQuestion: its first question
      return [{ kind: 'tool' as const, text: `${b.name}(${String(arg).slice(0, 160)})`, name: b.name, id: b.id, input: i }];
    }
    if (b.type === 'tool_result') {
      const t = Array.isArray(b.content) ? b.content.map((x: any) => x.text ?? '').join('\n') : String(b.content ?? '');
      if (/^Todos have been modified successfully/.test(t)) return [];  // the checklist above already shows it
      return [{ kind: 'result' as const, text: t, id: b.tool_use_id }];
    }
    return [];
  });
}

// ---- live connection (phase 2): one WebSocket per machine ----
export type LiveEvent =
  | { type: 'hello'; machine: string }
  | { type: 'subagents'; entry: string }
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
  | { type: 'session_reset'; entry: string; session_id: string }
  | { type: 'tasks'; entry: string; tasks: Task[] }
  | { type: 'btw'; entry: string; kind: 'asked' | 'delta' | 'answer' | 'done' | 'closed'; text?: string; error?: boolean }
  | { type: 'queued'; entry: string; count: number; sent?: number; dropped?: boolean }
  | ({ type: 'usage'; entry: string } & Usage)
  | { type: 'inbox'; item: InboxItem }
  | { type: 'inbox_read'; id: number | null }
  | { type: 'inbox_removed'; id: number }
  | { type: 'settings' }
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
  send(entry: string, text: string, images: Image[] = [], now = false) { this.sendJson({ type: 'send', entry, text, images, ...(now ? { now } : {}) }); }
  interrupt(entry: string) { this.sendJson({ type: 'interrupt', entry }); }
  stop(entry: string) { this.sendJson({ type: 'stop', entry }); }
  stopTask(entry: string, task: string) { this.sendJson({ type: 'stop_task', entry, task_id: task }); }
  btw(entry: string, text: string) { this.sendJson({ type: 'btw', entry, text }); }
  btwClose(entry: string) { this.sendJson({ type: 'btw_close', entry }); }
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
