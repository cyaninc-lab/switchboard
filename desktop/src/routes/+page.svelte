<script lang="ts">
  import * as api from '$lib/api';
  import { pairLocal, serviceStatus, serviceInstall, serviceUninstall, inShell, openExternal, openLocal, appVersion, checkForUpdate, notify } from '$lib/shell';
  import { backdrop } from '$lib/backdrop';
  import { theme, THEMES, applyTheme, fonts, UI_FONTS, MONO_FONTS, SIZES, applyFonts } from '$lib/themes.svelte';
  applyTheme(theme.name);
  applyFonts();

  // links (in Claude's replies, the inbox, questions) never navigate this window: web and mail links open in the
  // default browser / mail app, anything else is ignored. ponytail: until sessions get windows or tabs of their own
  function onLinkClick(ev: MouseEvent) {
    const a = (ev.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
    if (!a) return;
    const url = new URL(a.href, location.href);
    if (url.origin === location.origin && !a.target) return;  // the app's own links
    ev.preventDefault();
    if (['http:', 'https:', 'mailto:'].includes(url.protocol)) openExternal(url.href);
  }
  // file paths in replies (inline code the markdown marked .path): open on this machine, else copy the path
  let toast = $state('');
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  function say(t: string) { toast = t; clearTimeout(toastTimer); toastTimer = setTimeout(() => (toast = ''), 3000); }
  const isLocal = (m: Machine) => ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(m.url).hostname);
  async function onPathClick(ev: MouseEvent) {
    const el = (ev.target as Element | null)?.closest?.('code.path');
    if (!el || !selected || el.closest('pre')) return;
    const raw = el.textContent!.trim().replace(/:\d+(:\d+)?$/, '');
    const cwd = selected.entry.session?.cwd ?? '';
    const abs = /^(~[\/]|\/|[A-Za-z]:[\\/]|\\\\)/.test(raw) ? raw : `${cwd.replace(/[\/\\]$/, '')}/${raw.replace(/^\.\//, '')}`;
    if (inShell() && isLocal(selected.machine)) {
      try { if ((await openLocal(abs)) === 'revealed') say(`Shown in its folder: ${abs}`); }
      catch (e) { say(String(e)); }
    } else {
      try { await navigator.clipboard.writeText(abs); say(`Copied: ${abs}${inShell() ? ` (it's on ${selected.machine.name})` : ''}`); }
      catch { say(abs); }
    }
  }
  $effect(() => {
    document.addEventListener('click', onPathClick);
    document.addEventListener('click', onLinkClick, true);
    document.addEventListener('auxclick', onLinkClick, true);  // middle-click too
    return () => { document.removeEventListener('click', onPathClick); document.removeEventListener('click', onLinkClick, true); document.removeEventListener('auxclick', onLinkClick, true); };
  });
  import type { Update } from '$lib/shell';
  import { captureAppLog, buildReport, issueUrl } from '$lib/report';
  import type { ServiceStatus } from '$lib/shell';
  import TerminalView from '$lib/Terminal.svelte';
  import InboxView from '$lib/Inbox.svelte';
  import WatchesView from '$lib/Watches.svelte';
  import PromptCard from '$lib/Prompt.svelte';
  import { md } from '$lib/markdown';
  import { tick } from 'svelte';
  import type { Machine, Entry, Session, HistoryMessage } from '$lib/api';

  // the HTML autofocus attribute only works on page load, not on elements Svelte adds later
  const focusOnMount = (node: HTMLElement) => { node.focus(); };

  // ---- state ----
  let machines = $state<Machine[]>(api.loadMachines());
  let tree = $state<Record<string, { entries: Entry[]; error?: string }>>({});
  let selected = $state<{ machine: Machine; entry: Entry; sub?: api.Sub } | null>(null);
  let messages = $state<HistoryMessage[]>([]);
  let loadingHistory = $state(false);
  let openSeq = 0;
  let filter = $state('');
  let dialog = $state<'connect' | 'add' | 'settings' | 'report' | 'watches' | null>(null);

  // inbox: per machine, merged newest first
  let inboxBy = $state<Record<string, api.InboxItem[]>>({});
  let showInbox = $state(true);
  let inboxItems = $derived(machines.flatMap((m) => (inboxBy[m.url] ?? []).map((i) => ({ ...i, machine: m.name, url: m.url }))).sort((a, b) => b.created - a.created));
  let unreadCount = $derived(inboxItems.filter((i) => !i.read).length);
  async function loadInbox(m: Machine) { try { inboxBy[m.url] = await api.inbox(m); } catch {} }
  async function readItem(i: { url: string; id: number }) {
    const m = machines.find((x) => x.url === i.url);
    if (m) { await api.markRead(m, i.id); loadInbox(m); }
  }
  async function deleteItem(i: { url: string; id: number }) {
    const m = machines.find((x) => x.url === i.url);
    if (m) { await api.deleteInboxItem(m, i.id).catch(() => {}); loadInbox(m); }
  }
  async function readAll() { for (const m of machines) { await api.markRead(m).catch(() => {}); loadInbox(m); } }
  function openFromInbox(i: { url: string; entry_id: string | null; id: number }) {
    const m = machines.find((x) => x.url === i.url);
    const e = m && tree[m.url]?.entries.find((x) => x.id === i.entry_id);
    if (m && e) open(m, e);
    readItem(i);
  }
  if (typeof window !== 'undefined') captureAppLog();

  // self-update: check shortly after start and every 6 hours; never installs without a click
  let update = $state<Update | null>(null);
  let updNotes = $state(false);
  let updBusy = $state<number | null>(null);
  let updMsg = $state('');
  let version = $state('');
  async function checkUpdates(manual = false) {
    try {
      update = await checkForUpdate();
      if (manual) updMsg = update ? `Switchboard ${update.version} is available.` : "You're on the latest version.";
    } catch (e) { if (manual) updMsg = `Couldn't check for updates: ${(e as Error).message ?? e}`; }
  }
  $effect(() => {
    appVersion().then((v) => (version = v));
    const first = setTimeout(() => checkUpdates(), 10_000);
    const every = setInterval(() => checkUpdates(), 6 * 3600_000);
    return () => { clearTimeout(first); clearInterval(every); };
  });
  let liveHere = $derived(Object.values(tree).flatMap((t) => t.entries).filter((e) => ['working', 'waiting', 'ready'].includes(e.state)).length);
  async function installUpdate() {
    if (!update) return;
    updBusy = 0;
    try { await update.install((p) => (updBusy = p)); }
    catch (e) { updBusy = null; updMsg = `Update failed: ${(e as Error).message ?? e}`; }
  }

  // report a bug
  let rDesc = $state('');
  let rLogs = $state(true);
  let rBusy = $state(false);
  let rDone = $state('');
  let rReport = $state('');
  async function sendReport(ev: Event) {
    ev.preventDefault();
    rBusy = true; rDone = '';
    rReport = await buildReport(rDesc, machines, rLogs, await appVersion());
    let copied = true;
    try { await navigator.clipboard.writeText(rReport); } catch { copied = false; }
    await openExternal(issueUrl(rDesc));
    rDone = copied ? 'Report copied. A new GitHub issue opened in your browser: paste (Ctrl+V) and submit.'
                   : "Couldn't copy automatically: select the report below, copy it, and paste it into the issue that opened.";
    rBusy = false;
  }

  // settings: background service
  let svc = $state<ServiceStatus | null>(null);
  let svcBusy = $state(false);
  let svcError = $state('');
  // settings: other devices (this machine's daemon over Tailscale, and pairing codes)
  let net = $state<api.Network | null>(null);
  let netBusy = $state(false);
  let netError = $state('');
  let code = $state<{ code: string; until: number } | null>(null);
  let now = $state(Date.now());
  const localMachine = $derived(machines.find((m) => api.isLocal(m)) ?? null);
  async function loadNet() { if (localMachine) try { net = await api.network(localMachine); } catch (e) { netError = (e as Error).message; } }
  async function toggleNet() {
    if (!localMachine || !net) return;
    netBusy = true; netError = '';
    try { net = await api.setNetwork(localMachine, !net.listen_tailscale); } catch (e) { netError = (e as Error).message; }
    netBusy = false;
  }
  async function newCode() {
    if (!localMachine) return;
    try { const c = await api.pairCode(localMachine); code = { code: c.code, until: Date.now() + c.expires_in * 1000 }; }
    catch (e) { netError = (e as Error).message; }
  }
  $effect(() => { if (!code) return; const t = setInterval(() => (now = Date.now()), 1000); return () => clearInterval(t); });
  async function openSettings() {
    loadNet(); code = null;
    dialog = 'settings'; svcError = '';
    accMachine = accMachine ?? machines[0] ?? null;
    if (accMachine) loadAccounts(accMachine);
    svc = await serviceStatus();
  }
  async function toggleService() {
    if (!svc) return;
    const liveHere = Object.values(tree).flatMap((t) => t.entries).filter((e) => ['working', 'waiting', 'ready'].includes(e.state)).length;
    if (liveHere && svc.embedded && !svc.installed) svcError = `${liveHere} live session(s) will stop during the switch; they resume on your next message.`;
    svcBusy = true;
    try {
      await (svc.installed ? serviceUninstall() : serviceInstall());
      await ensureLocal(); await refresh();
    } catch (e) { svcError = String(e); }
    svc = await serviceStatus();
    svcBusy = false;
  }

  // connect dialog
  let cUrl = $state('http://127.0.0.1:7878');
  let cCode = $state('');
  let cError = $state('');

  // add dialog
  let aMachine = $state<Machine | null>(null);
  let aQuery = $state('');
  let aSessions = $state<Session[]>([]);
  let aTotal = $state(0);
  let aPicks = $state<Session[]>([]);  // several can be added at once
  const aPick = $derived(aPicks.length === 1 ? aPicks[0] : null);
  let aName = $state('');
  // project for what's being added: '' = its folder's name (the daemon's default), an existing project, or a new one
  let aProj = $state('');
  let aNewProj = $state('');
  const aGroup = $derived(aProj === '\u0000new' ? aNewProj.trim() : aProj);
  const projects = $derived(aMachine ? [...new Set((tree[aMachine.url]?.entries ?? []).map((e) => e.group))].sort((x, y) => x.localeCompare(y)) : []);
  let aError = $state('');
  let aTab = $state<'existing' | 'new'>('existing');
  let nFolder = $state('');
  let nTyped = false; // the suggested folder arrives async: never let it land in the middle of someone's typing
  let nFolders = $state<string[]>([]);
  let nName = $state('');
  let nMode = $state('default');
  let nModel = $state('default');
  let nAccount = $state('');
  async function newTab() {
    aTab = 'new'; aError = '';
    if (!aMachine) return;
    loadModels(aMachine); loadAccounts(aMachine);
    try { const f = await api.folders(aMachine); nFolders = f.folders; if (!nTyped && !nFolder) nFolder = f.folders[1] ?? f.home; } catch (e) { aError = (e as Error).message; }
  }
  async function createNew(ev: Event) {
    ev.preventDefault();
    if (!aMachine) return;
    try {
      const e = await api.addEntry(aMachine, { new: { cwd: nFolder.trim() }, name: nName.trim() || undefined, group: aGroup || undefined, mode: nMode, model: nModel === 'default' ? undefined : nModel, account: nAccount || undefined });
      dialog = null; nName = ''; aProj = ''; aNewProj = '';
      await refresh();
      open(aMachine, e);
    } catch (e) { aError = (e as Error).message === 'no_such_folder' ? `No folder ${nFolder} on ${aMachine.name}` : (e as Error).message; }
  }

  // ---- live sessions (phase 2) ----
  const lives = new Map<string, api.Live>();
  let streaming = $state<Record<string, string>>({});
  let thinkingLive = $state<Record<string, string>>({});
  // subagents: their messages carry parent_tool_use_id; show them inside the Agent step that started them
  let mainMsgs = $derived(messages.filter((m) => !m.parent_tool_use_id));
  let byParent = $derived(messages.reduce((acc, m) => {
    if (m.parent_tool_use_id) (acc[m.parent_tool_use_id] ??= []).push(m);
    return acc;
  }, {} as Record<string, HistoryMessage[]>));
  const AGENT_TOOLS = ['Agent', 'Task'];
  let queued = $state<Record<string, number>>({});
  // ponytail: per-window and in memory, a restart forgets it; move to the daemon if several clients need to agree
  let unread = $state<Record<string, boolean>>({});
  // waiting > working > unread > the entry's own state
  const icon = (e: Entry) => (e.state === 'waiting' || e.state === 'working' ? e.state : unread[e.id] ? 'unread' : e.state);
  let expanded = $state<Record<string, boolean>>({});  // tool output shown in full
  const RESULT_LINES = 6;
  let prompts = $state<Record<string, { request_id: string; tool: string; kind: 'tool' | 'question' | 'plan'; input: any; remember: string[] }[]>>({});
  let draft = $state('');
  // each session (and subagent view) keeps its own unsent text and images; ponytail: in memory, a restart forgets it
  const drafts: Record<string, { text: string; images: typeof attachments }> = {};
  // messages sent while Claude works wait at the bottom (as in Claude Code) and join the chat when the daemon sends them
  // ponytail: mirrors only this window's queue; another device queueing at the same time can make the counts disagree
  let pendingQ = $state<Record<string, HistoryMessage[]>>({});
  const msgText = (m: HistoryMessage) => (Array.isArray(m.message.content) ? m.message.content : []).filter((b: any) => b.type === 'text').map((b: any) => b.text).join('\n');
  function giveBack(entryId: string, texts: string[]) {  // queued text that won't be sent goes back in the box
    const t = texts.filter((x) => x.trim()).join('\n');
    if (!t) return;
    const join = (cur: string) => (cur ? `${t}\n${cur}` : t);
    if (selected?.entry.id === entryId && !selected.sub) draft = join(draft);
    else drafts[entryId] = { text: join(drafts[entryId]?.text ?? ''), images: drafts[entryId]?.images ?? [] };
  }
  function swapDraft(next: string) {
    if (selected) drafts[selected.entry.id + (selected.sub ? '/' + selected.sub.id : '')] = { text: draft, images: attachments };
    draft = drafts[next]?.text ?? '';
    attachments = drafts[next]?.images ?? [];
  }
  // follow new output while the view is at the bottom; leave it alone once the user scrolls up to read
  let logEl = $state<HTMLDivElement>();
  let stick = true;
  // only the user's own scrolling (wheel, touch, keys, scrollbar drag) can unstick; resizes and new content can't
  let userScroll = 0;
  const markUser = () => { userScroll = Date.now(); };
  const onLogScroll = () => {
    if (!logEl) return;
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 80;
    if (atBottom) stick = true;
    else if (Date.now() - userScroll < 600) stick = false;
    else if (stick) logEl.scrollTop = logEl.scrollHeight;
  };
  $effect(() => {
    messages.length; selected && streaming[selected.entry.id]; selected && prompts[selected.entry.id]?.length;
    if (stick) tick().then(() => logEl && (logEl.scrollTop = logEl.scrollHeight));
  });
  // content can also grow after rendering (an image loading, a diff expanding): keep following while at the bottom
  let logBody = $state<HTMLDivElement>();
  $effect(() => {
    if (!logBody) return;
    const ro = new ResizeObserver(() => { if (stick && logEl) logEl.scrollTop = logEl.scrollHeight; });
    ro.observe(logBody);
    if (logEl) ro.observe(logEl);  // the window resizing too
    return () => ro.disconnect();
  });
  // attachments (images) and the slash-command menu
  let attachments = $state<(api.Image & { url: string; name: string })[]>([]);
  let cmdList = $state<Record<string, api.Command[]>>({});
  let modelList = $state<Record<string, api.Model[]>>({});
  let acctList = $state<Record<string, api.Account[]>>({});
  async function loadAccounts(m: Machine) { try { acctList[m.url] = await api.accounts(m); } catch {} }
  // settings: accounts on a machine
  let accMachine = $state<Machine | null>(null);
  let accName = $state('');
  let accToken = $state('');
  let accKind = $state<'oauth' | 'api_key'>('oauth');
  let accError = $state('');
  async function addAccount(ev: Event) {
    ev.preventDefault();
    if (!accMachine) return;
    try { acctList[accMachine.url] = await api.addAccount(accMachine, accName.trim(), accToken.trim(), accKind); accName = ''; accToken = ''; accError = ''; }
    catch (e) { accError = (e as Error).message === 'bad_account' ? 'Give it a name and paste a token' : (e as Error).message; }
  }
  async function removeAccount(name: string) {
    if (!accMachine) return;
    await api.removeAccount(accMachine, name);
    loadAccounts(accMachine);
    refresh();
  }
  async function loadModels(m: Machine) { if (!modelList[m.url]) try { modelList[m.url] = await api.models(m); } catch {} }
  let cmdIdx = $state(0);
  let fileInput = $state<HTMLInputElement>();
  const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
  async function addFiles(files: Iterable<File>) {
    for (const f of files) {
      if (!IMAGE_TYPES.includes(f.type)) { liveErr = `${f.name || 'That file'} isn't a PNG, JPEG, GIF or WebP image`; continue; }
      if (f.size > 5_000_000) { liveErr = `${f.name || 'Image'} is over 5 MB`; continue; }
      const url = await new Promise<string>((ok) => { const r = new FileReader(); r.onload = () => ok(r.result as string); r.readAsDataURL(f); });
      attachments = [...attachments, { media_type: f.type, data: url.split(',')[1], url, name: f.name || 'pasted image' }];
    }
  }
  function onPaste(ev: ClipboardEvent) {
    const files = [...(ev.clipboardData?.items ?? [])].filter((i) => i.kind === 'file').map((i) => i.getAsFile()!).filter(Boolean);
    if (files.length) { ev.preventDefault(); addFiles(files); }
  }
  function onDrop(ev: DragEvent) { ev.preventDefault(); if (ev.dataTransfer?.files.length) addFiles(ev.dataTransfer.files); }
  async function loadCommands(m: Machine) { try { cmdList[m.url] = await api.commands(m); } catch {} }
  let menu = $derived.by(() => {
    if (!selected || !/^\/\S*$/.test(draft)) return [];
    const q = draft.slice(1).toLowerCase();
    return (cmdList[selected.machine.url] ?? []).filter((c) => c.name.toLowerCase().includes(q))
      .sort((a, b) => Number(!a.name.toLowerCase().startsWith(q)) - Number(!b.name.toLowerCase().startsWith(q))).slice(0, 8);
  });
  function complete(c: api.Command) { draft = `/${c.name} `; cmdIdx = 0; }
  // multi-line box: Enter sends, Shift+Enter starts a new line, Ctrl+Enter sends now (interrupts), as in Claude Code
  let sendNow = false;
  function enterSends(ev: KeyboardEvent) {
    if (ev.key !== 'Enter' || ev.shiftKey || ev.isComposing || ev.defaultPrevented) return;
    ev.preventDefault();
    sendNow = ev.ctrlKey || ev.metaKey;
    (ev.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
  }
  function grow(el: HTMLTextAreaElement) {  // fit the text, up to the CSS max-height
    const fit = () => { el.style.height = 'auto'; el.style.height = `${el.scrollHeight}px`; };
    $effect(() => { void draft; fit(); });  // also shrinks back after sending
  }
  function onInputKey(ev: KeyboardEvent) {
    if (!menu.length) return enterSends(ev);
    if (ev.key === 'ArrowDown') { ev.preventDefault(); cmdIdx = (cmdIdx + 1) % menu.length; }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); cmdIdx = (cmdIdx - 1 + menu.length) % menu.length; }
    else if (ev.key === 'Tab' || (ev.key === 'Enter' && draft !== `/${menu[cmdIdx]?.name}`)) { ev.preventDefault(); complete(menu[Math.min(cmdIdx, menu.length - 1)]); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); draft = ''; }
    enterSends(ev);
  }
  let liveErr = $state('');
  const LIVE_STATES = ['working', 'waiting', 'ready'];
  // real-terminal toggle: which entry shows the claude TUI, and who receives its output
  let termFor = $state<string | null>(null);
  let termSink: ((data: string) => void) | null = null;

  $effect(() => {
    for (const m of machines) if (!lives.has(m.url)) { lives.set(m.url, new api.Live(m, (e) => onEvent(e, m), () => {})); loadInbox(m); }
    for (const [url, l] of lives) if (!machines.some((m) => m.url === url)) { l.close(); lives.delete(url); }
  });

  function setEntry(id: string, patch: Partial<Entry>) {
    for (const t of Object.values(tree)) { const e = t.entries.find((x) => x.id === id); if (e) Object.assign(e, patch); }
    if (selected?.entry.id === id) Object.assign(selected.entry, patch);
  }
  function onEvent(e: api.LiveEvent, m?: Machine) {
    if (e.type === 'inbox' && m) {
      inboxBy[m.url] = [e.item, ...(inboxBy[m.url] ?? [])];
      notify(`${e.item.source}${e.item.from_agent ? ' (agent)' : ''}`, `${e.item.title}: ${e.item.body}`);
      return;
    }
    if ((e.type === 'inbox_read' || e.type === 'inbox_removed') && m) { loadInbox(m); return; }
    if (e.type === 'subagents') { refresh(); if (selected?.sub && selected.entry.id === e.entry) loadSub(); return; }
    if (e.type === 'state') {
      const was = Object.values(tree).flatMap((t) => t.entries).find((x) => x.id === e.entry)?.state;
      setEntry(e.entry, { state: e.state as Entry['state'] });
      if (was && !LIVE_STATES.includes(was) && LIVE_STATES.includes(e.state)) refresh();  // which subagents run is the daemon's call
      if (e.state !== 'working' && e.state !== 'waiting') {
        streaming[e.entry] = ''; thinkingLive[e.entry] = ''; queued[e.entry] = 0;
        if (pendingQ[e.entry]?.length) { giveBack(e.entry, pendingQ[e.entry].map(msgText)); pendingQ[e.entry] = []; }  // the session ended with them unsent
      }
    } else if (e.type === 'delta') {
      if (e.kind === 'thinking') thinkingLive[e.entry] = (thinkingLive[e.entry] ?? '') + e.text;
      else streaming[e.entry] = (streaming[e.entry] ?? '') + e.text;
    } else if (e.type === 'message') {
      if (selected?.entry.id === e.entry && !selected.sub) messages = [...messages, e.message];
      if (e.message.type === 'assistant') { streaming[e.entry] = ''; thinkingLive[e.entry] = ''; if (selected?.entry.id !== e.entry || selected.sub) unread[e.entry] = true; }
    } else if (e.type === 'permission_request') {
      prompts[e.entry] = [...(prompts[e.entry] ?? []), { request_id: e.request_id, tool: e.tool, kind: e.kind ?? 'tool', input: e.input, remember: e.remember ?? [] }];
      // a session you're not looking at needs you: say so outside the app too
      if (typeof document !== 'undefined' && (!document.hasFocus() || selected?.entry.id !== e.entry)) {
        const name = Object.values(tree).flatMap((t) => t.entries).find((x) => x.id === e.entry)?.name ?? 'A session';
        notify(`${name} needs you`, e.kind === 'question' ? (e.input?.questions?.[0]?.question ?? 'Claude has a question')
          : e.kind === 'plan' ? 'Claude has a plan for you to approve' : `Allow ${e.tool}: ${summary(e.input)}?`);
      }
    } else if (e.type === 'permission_resolved') {
      prompts[e.entry] = (prompts[e.entry] ?? []).filter((p) => p.request_id !== e.request_id);
    } else if (e.type === 'mode') {
      setEntry(e.entry, { mode: e.mode });
    } else if (e.type === 'effort') {
      setEntry(e.entry, { effort: e.effort });
    } else if (e.type === 'model') {
      setEntry(e.entry, { model: e.model });
    } else if (e.type === 'account') {
      setEntry(e.entry, { account: e.account });
    } else if (e.type === 'usage') {
      setEntry(e.entry, { usage: e });
    } else if (e.type === 'queued') {
      queued[e.entry] = e.count;
      const q = pendingQ[e.entry] ?? [];
      if (e.sent) {
        if (selected?.entry.id === e.entry && !selected.sub) messages = [...messages, ...q.slice(0, e.sent)];
        pendingQ[e.entry] = q.slice(e.sent);
      } else if (e.dropped) { giveBack(e.entry, q.map(msgText)); pendingQ[e.entry] = []; }
    } else if (e.type === 'commands') {
      if (selected) loadCommands(selected.machine);
    } else if (e.type === 'notice') {
      if (selected?.entry.id === e.entry && !selected.sub) messages = [...messages, { type: 'assistant', uuid: crypto.randomUUID(), message: { content: [{ type: 'text', text: `— ${e.text} —` }] } }];
    } else if (e.type === 'terminal_output') {
      if (e.entry === termFor) termSink?.(e.data);
    } else if (e.type === 'terminal_closed') {
      if (e.entry === termFor) { termFor = null; if (selected) open(selected.machine, selected.entry); }  // back to chat, fresh history
    } else if (e.type === 'error') {
      if (!e.entry || e.entry === selected?.entry.id) liveErr = e.message;
    }
  }
  const live = () => (selected ? lives.get(selected.machine.url) : undefined);
  function sendDraft(ev: Event) {
    ev.preventDefault();
    if (!selected || (!draft.trim() && !attachments.length)) return;
    try {
      const images = attachments.map(({ media_type, data }) => ({ media_type, data }));
      live()!.send(selected.entry.id, draft, images, sendNow);  // now: the daemon interrupts and sends this first
      const content = [...images.map((im) => ({ type: 'image', source: { type: 'base64', ...im } })), { type: 'text', text: draft }];
      const msg: HistoryMessage = { type: 'user', uuid: crypto.randomUUID(), message: { role: 'user', content } };
      const id = selected.entry.id;
      if (selected.entry.state === 'working' || selected.entry.state === 'waiting') pendingQ[id] = sendNow ? [msg, ...(pendingQ[id] ?? [])] : [...(pendingQ[id] ?? []), msg];
      else messages = [...messages, msg];
      draft = ''; attachments = []; liveErr = '';
    } catch (e) { liveErr = (e as Error).message; }
    sendNow = false;
  }
  function sendSub(ev: Event) {  // Claude Code's way: the parent session relays it with SendMessage
    ev.preventDefault();
    const s = selected;
    if (!s?.sub || !draft.trim()) return;
    try {
      live()!.send(s.entry.id, `Use the SendMessage tool to send this message to agent ${s.sub.id} (it resumes the subagent if it has finished). Send it as written and do nothing else:\n\n${draft}`, []);
      messages = [...messages, { type: 'user', uuid: crypto.randomUUID(), message: { role: 'user', content: draft } }];
      draft = ''; liveErr = '';
    } catch (e) { liveErr = (e as Error).message; }
  }
  function act(fn: (l: api.Live, id: string) => void) {
    if (!selected) return;
    try { fn(live()!, selected.entry.id); liveErr = ''; } catch (e) { liveErr = (e as Error).message; }
  }
  function onKey(ev: KeyboardEvent) {
    if (ev.key === 'Escape' && selected?.entry.state === 'working' && !dialog) act((l, id) => l.interrupt(id));
  }
  const summary = (input: any) => input?.command ?? input?.file_path ?? input?.pattern ?? input?.url ?? JSON.stringify(input).slice(0, 200);

  // ---- tree ----
  async function refresh() {
    const next: typeof tree = {};
    await Promise.all(machines.map(async (m) => {
      try { next[m.url] = { entries: await api.entries(m) }; }
      catch (e) { next[m.url] = { entries: tree[m.url]?.entries ?? [], error: String((e as Error).message) }; }
    }));
    tree = next;
  }
  // In the desktop app, this machine's daemon is connected automatically (no code to type),
  // and re-paired if its token stopped working (e.g. its data was reset).
  const LOCAL = 'http://127.0.0.1:7878';
  async function ensureLocal() {
    const known = machines.find((m) => m.url === LOCAL);
    if (known) {
      try { await api.entries(known); return; } catch (e) { if ((e as Error).message !== 'unauthorized') return; }
    }
    try {
      const m = await pairLocal();
      if (!m) return;
      machines = [m, ...machines.filter((x) => x.url !== LOCAL)];
      api.saveMachines(machines);
    } catch (e) { console.error('local daemon:', e); }
  }
  $effect(() => {
    ensureLocal().then(refresh);
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  });

  function groups(m: Machine) {
    const es = (tree[m.url]?.entries ?? []).filter((e) => !filter || (e.name + ' ' + e.group).toLowerCase().includes(filter.toLowerCase()));
    const g: Record<string, Entry[]> = {};
    for (const e of es) (g[e.group] ??= []).push(e);
    return Object.entries(g);
  }

  async function open(machine: Machine, entry: Entry) {
    if (termFor && termFor !== entry.id) termFor = null;
    swapDraft(entry.id);
    selected = { machine, entry };
    unread[entry.id] = false;
    stick = true;
    if (!cmdList[machine.url]) loadCommands(machine);
    loadModels(machine);
    loadAccounts(machine);
    loadingHistory = true;
    messages = [];
    const mine = ++openSeq;
    let hist: HistoryMessage[] = [];
    try { hist = await api.history(machine, entry.id); } catch {}
    if (mine !== openSeq) return;  // the user already opened something else
    messages = [...hist, ...messages];  // keep anything sent or streamed in while the history was loading
    loadingHistory = false;
  }

  // ---- subagents: running ones nest under their session in the tree; finished ones open from their Agent step ----
  // the daemon decides (0.1.9+); an older daemon doesn't send `running`
  const subRunning = (e: Entry, a: api.Sub) => LIVE_STATES.includes(e.state) && (a.running ?? (!a.finished && !a.stopped));
  // folded tree parts (project groups 'g:<machine>|<group>', subagent lists by entry id), remembered on this device
  const FOLD_KEY = 'switchboard.folded';
  let subOpen = $state<Record<string, boolean>>((() => { try { return JSON.parse(localStorage.getItem(FOLD_KEY) || '{}'); } catch { return {}; } })());
  $effect(() => { const v = JSON.stringify(subOpen); try { localStorage.setItem(FOLD_KEY, v); } catch {} });

  // ---- right-click menu on a tree entry ----
  // the menu is for a session (entry) or a project (group)
  let ctx = $state<{ x: number; y: number; machine: Machine; entry?: Entry; group?: string } | null>(null);
  function openMenu(ev: MouseEvent, machine: Machine, entry?: Entry, group?: string) {
    ev.preventDefault();
    // from the keyboard (menu key) there's no pointer position: put it under the row
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const at = ev.clientX || ev.clientY ? { x: ev.clientX, y: ev.clientY } : { x: r.left + 24, y: r.bottom };
    ctx = { ...at, machine, entry, group };
  }

  // ---- rename in place (sessions and projects) ----
  let renaming = $state<{ key: string; value: string } | null>(null);  // key: 'e:<entry id>' or 'g:<machine>|<group>'
  function startRename(key: string, value: string) { ctx = null; renaming = { key, value }; }
  async function commitRename(m: Machine, entry?: Entry, group?: string) {
    const name = renaming?.value.trim();
    renaming = null;
    if (!name) return;
    try {
      if (entry && name !== entry.name) { await api.updateEntry(m, entry.id, { name }); setEntry(entry.id, { name }); }
      // a project is the group name its sessions share: renaming it moves them all
      if (group !== undefined && name !== group)
        await Promise.all((tree[m.url]?.entries ?? []).filter((e) => e.group === group).map((e) => api.updateEntry(m, e.id, { group: name })));
    } catch (e) { liveErr = (e as Error).message; }
    await refresh();
  }
  function renameKeys(ev: KeyboardEvent, m: Machine, entry?: Entry, group?: string) {
    if (ev.key === 'Enter') { ev.preventDefault(); commitRename(m, entry, group); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); renaming = null; }
  }
  const focusSelect = (el: HTMLInputElement) => { el.focus(); el.select(); };
  function closeSession(m: Machine, e: Entry) {
    ctx = null;
    try { lives.get(m.url)!.stop(e.id); liveErr = ''; } catch (err) { liveErr = (err as Error).message; }
  }
  function kids(e: Entry, parent: string | null) {
    const all = (e.subagents ?? []).filter((a) => subRunning(e, a));
    const ids = new Set(all.map((a) => a.id));
    // a subagent whose parent isn't listed still shows, at the top
    return all.filter((a) => (parent === null ? !a.parent || !ids.has(a.parent) : a.parent === parent));
  }
  async function openSub(machine: Machine, entry: Entry, sub: api.Sub) {
    if (termFor) termFor = null;
    swapDraft(entry.id + '/' + sub.id);
    selected = { machine, entry, sub };
    stick = true;
    messages = [];
    loadingHistory = true;
    await loadSub();
    loadingHistory = false;
  }
  async function loadSub() {
    const s = selected;
    if (!s?.sub) return;
    const mine = ++openSeq;
    try { const h = await api.subagentHistory(s.machine, s.entry.id, s.sub.id); if (mine === openSeq) messages = h; } catch {}
  }
  $effect(() => {  // a running subagent's view follows along (its steps are written to its own transcript)
    const s = selected;
    if (!s?.sub) return;
    const t = setInterval(loadSub, 2500); // ponytail: polls even a finished one, since a message can resume it; a local file read
    return () => clearInterval(t);
  });
  $effect(() => {  // keep the open subagent's status current after a tree refresh
    const s = selected;
    if (!s?.sub) return;
    const fresh = tree[s.machine.url]?.entries.find((x) => x.id === s.entry.id)?.subagents?.find((a) => a.id === s.sub!.id);
    if (fresh && (fresh.finished !== s.sub.finished || fresh.stopped !== s.sub.stopped)) { s.sub.finished = fresh.finished; s.sub.stopped = fresh.stopped; loadSub(); }
  });

  const stateLabel: Record<string, string> = {
    working: 'Working', waiting: 'Waiting for you', new: 'New: send a message to start it', stopped: 'Stopped: send a message to resume it', ready: 'Live and ready', unread: 'New message',
    terminal: 'Open in a terminal (read-only here)', missing: 'Session no longer on this machine', error: 'Error',
  };

  // ---- connect ----
  async function connect(ev: Event) {
    ev.preventDefault();
    cError = '';
    try {
      const m = await api.pair(cUrl.trim(), cCode.trim(), 'Switchboard desktop');
      machines = [...machines.filter((x) => x.url !== m.url), m];
      api.saveMachines(machines);
      dialog = null; cCode = '';
      refresh();
    } catch (e) { cError = (e as Error).message; }
  }
  function disconnect(m: Machine) {
    machines = machines.filter((x) => x.url !== m.url);
    api.saveMachines(machines);
    if (selected?.machine.url === m.url) selected = null;
    refresh();
  }

  // ---- add ----
  async function openAdd() {
    dialog = 'add'; aError = ''; aPicks = []; nTyped = false; aProj = ''; aNewProj = '';
    aMachine = aMachine ?? machines[0] ?? null;
    await search();
  }
  async function search() {
    if (!aMachine) return;
    try { const r = await api.sessions(aMachine, aQuery, 30); aSessions = r.sessions; aTotal = r.total; }
    catch (e) { aError = (e as Error).message; }
  }
  function pick(s: Session) {  // toggles: pick several to add them together
    if (s.entry_id) return;
    aPicks = aPicks.some((x) => x.session_id === s.session_id) ? aPicks.filter((x) => x.session_id !== s.session_id) : [...aPicks, s];
    if (aPicks.length === 1) aName = aPicks[0].custom_title || aPicks[0].summary || 'Untitled';
  }
  async function add(ev: Event) {
    ev.preventDefault();
    const m = aMachine;
    if (!m || !aPicks.length) return;
    try {
      // one: the name typed here; several: each keeps its own title. The group applies to all.
      const added = [];
      for (const s of aPicks)
        added.push(await api.addEntry(m, { session_id: s.session_id, name: aPicks.length === 1 ? aName : undefined, group: aGroup || undefined }));
      dialog = null; aProj = ''; aNewProj = '';
      await refresh();
      open(m, added[0]);
    } catch (e) { aError = (e as Error).message; }
  }
  async function remove() {
    if (!selected) return;
    await api.removeEntry(selected.machine, selected.entry.id);
    selected = null;
    refresh();
  }
  async function togglePin() {
    if (!selected) return;
    selected.entry = await api.updateEntry(selected.machine, selected.entry.id, { pinned: !selected.entry.pinned });
    refresh();
  }

  const ago = (ms: number) => {
    const s = (Date.now() - ms) / 1000;
    return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
  };
  let counts = $derived(Object.values(tree).flatMap((t) => t.entries).reduce((a, e) => ({ ...a, [e.state]: (a[e.state] ?? 0) + 1 }), {} as Record<string, number>));
</script>

<svelte:window onkeydown={(ev) => { if (ctx && ev.key === 'Escape') ctx = null; else onKey(ev); }} onclick={() => (ctx = null)} onblur={() => (ctx = null)} />

{#if ctx}
  {@const mm = ctx}
  {#if mm.entry}
    {@const me = mm.entry}
    <div class="ctxmenu" role="menu" aria-label="{me.name} actions" style="left: {mm.x}px; top: {mm.y}px">
      <button role="menuitem" onclick={() => startRename('e:' + me.id, me.name)}>Rename</button>
      <button role="menuitem" disabled={!LIVE_STATES.includes(me.state)} title="Ends the Claude process; it stays in the tree and resumes on your next message"
        onclick={() => closeSession(mm.machine, me)}>Close session</button>
    </div>
  {:else if mm.group !== undefined}
    {@const g = mm.group}
    <div class="ctxmenu" role="menu" aria-label="{g} project actions" style="left: {mm.x}px; top: {mm.y}px">
      <button role="menuitem" onclick={() => startRename(`g:${mm.machine.url}|${g}`, g)}>Rename project</button>
    </div>
  {/if}
{/if}

<div class="app">
  {#if update}
    <div class="updbar" role="status">
      <span><b>Switchboard {update.version}</b> is available{version ? ` (you have ${version})` : ''}.</span>
      {#if update.notes}<button class="link" onclick={() => (updNotes = !updNotes)}>What's new</button>{/if}
      {#if liveHere && svc?.embedded !== false}<span class="muted small">{liveHere} live session{liveHere === 1 ? '' : 's'} will stop and resume on your next message.</span>{/if}
      <span class="grow"></span>
      {#if updBusy !== null}<span class="muted small">Downloading… {updBusy}%</span>
      {:else}<button class="btn primary small" onclick={installUpdate}>Restart to update</button>
        <button class="tiny" aria-label="Dismiss update" onclick={() => (update = null)}>Later</button>{/if}
    </div>
    {#if updNotes}<div class="updnotes md">{@html md(update.notes)}</div>{/if}
  {/if}
  <header class="titlebar">
    <div class="brand">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--mauve)" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>
      Switchboard
    </div>
    <div class="chips">
      {#each machines as m (m.url)}
        <!-- online only once the daemon has actually answered; until then "connecting" (it may still be starting) -->
        <span class="chip" title={m.url}><span class="dot" class:on={tree[m.url] && !tree[m.url].error} class:off={!!tree[m.url]?.error}></span>{m.name}
          <span class="muted">{!tree[m.url] ? 'connecting…' : tree[m.url].error ? 'offline' : 'online'}</span></span>
      {/each}
      <button class="chip dashed" onclick={() => (dialog = 'connect')}>+ Connect a machine</button>
      <button class="chip icon" class:hot={unreadCount > 0} aria-label="Inbox ({unreadCount} unread)" title="Inbox" onclick={() => (showInbox = !showInbox)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5h13L22 12v7H2v-7z" /></svg>
        {#if unreadCount}<span class="count">{unreadCount}</span>{/if}
      </button>
      <button class="chip icon" aria-label="Report a bug" title="Report a bug" onclick={() => { dialog = 'report'; rDone = ''; rReport = ''; }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="8" y="6" width="8" height="14" rx="4" /><path d="M19 7l-3 2M5 7l3 2M19 19l-3-2M5 19l3-2M20 13h-4M4 13h4M10 4l1 2M14 4l-1 2" /></svg>
      </button>
      <button class="chip icon" aria-label="Settings" title="Settings" onclick={openSettings}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
      </button>
    </div>
  </header>

  <div class="body">
    {#snippet subs(m: Machine, e: Entry, parent: string | null, depth: number)}
      {#each kids(e, parent) as a (a.id)}
        <button class="entry sub" style="padding-left: {36 + depth * 14}px" class:sel={selected?.sub?.id === a.id && selected.entry.id === e.id}
          title="{a.type ?? 'subagent'}: {subRunning(e, a) ? 'running' : a.stopped ? 'stopped' : 'finished'}" onclick={() => openSub(m, e, a)}>
          <span class="st {subRunning(e, a) ? 'working' : 'stopped'}"></span>
          <span class="ename">↳ {a.description}</span>
          <span class="muted small">{ago(a.updated)}</span>
        </button>
        {@render subs(m, e, a.id, depth + 1)}
      {/each}
    {/snippet}
    <nav class="tree" aria-label="Sessions">
      <label class="search"><span class="sr">Filter</span><input type="search" placeholder="Filter…" bind:value={filter} /></label>
      <div class="scroll">
        {#if machines.length === 0}
          <p class="empty">No machines yet. Run <code>switchboard-daemon serve</code> and <code>switchboard-daemon pair</code>, then connect.</p>
        {/if}
        {#each machines as m (m.url)}
          <div class="machine" class:offline={!!tree[m.url]?.error}>
            <div class="mhead">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2" /><line x1="8" y1="20" x2="16" y2="20" /></svg>
              <span class="mname">{m.name}</span>
              <button class="tiny" title="Disconnect" aria-label="Disconnect {m.name}" onclick={() => disconnect(m)}>×</button>
            </div>
            {#each groups(m) as [g, es] (g)}
              {@const gk = `g:${m.url}|${g}`}
              {@const gOpen = subOpen[gk] ?? true}
              {#if renaming?.key === gk}
                <div class="group"><input class="rename" aria-label="Rename project {g}" bind:value={renaming.value} {@attach focusSelect}
                  onkeydown={(ev) => renameKeys(ev, m, undefined, g)} onblur={() => commitRename(m, undefined, g)} /></div>
              {:else}
              <button class="group" aria-expanded={gOpen} onclick={() => (subOpen[gk] = !gOpen)} oncontextmenu={(ev) => openMenu(ev, m, undefined, g)}>{gOpen ? '▾' : '▸'} {g}{#if !gOpen}<span class="muted small"> · {es.length}</span>
                {@const hint = es.some((e) => icon(e) === 'waiting') ? 'waiting' : es.some((e) => icon(e) === 'unread') ? 'unread' : null}
                {#if hint}<span class="st {hint} inline" title={stateLabel[hint]}></span>{/if}{/if}</button>
              {/if}
              {#each gOpen ? es : [] as e (e.id)}
                {#if renaming?.key === 'e:' + e.id}
                <div class="entry"><span class="st {icon(e)}"></span><input class="rename" aria-label="Rename {e.name}" bind:value={renaming.value} {@attach focusSelect}
                  onkeydown={(ev) => renameKeys(ev, m, e)} onblur={() => commitRename(m, e)} /></div>
                {:else}
                <button class="entry" class:sel={selected?.entry.id === e.id && !selected.sub} class:waiting={e.state === 'waiting'} title={stateLabel[icon(e)]} onclick={() => open(m, e)} oncontextmenu={(ev) => openMenu(ev, m, e)}>
                  <span class="st {icon(e)}"></span>
                  {#if e.pinned}<span class="pin" aria-label="pinned">★</span>{/if}
                  <span class="ename">{e.name}</span>
                  <span class="muted small">{e.session ? ago(e.session.last_modified) : ''}</span>
                </button>
                {/if}
                {@const running = (e.subagents ?? []).filter((a) => subRunning(e, a)).length}
                {#if running}
                  {@const shown = subOpen[e.id] ?? true}
                  <button class="subtoggle small" aria-expanded={shown} onclick={() => (subOpen[e.id] = !shown)}>
                    {shown ? '▾' : '▸'} {running} subagent{running === 1 ? '' : 's'} running
                  </button>
                  {#if shown}<div role="group" aria-label="Subagents of {e.name}">{@render subs(m, e, null, 1)}</div>{/if}
                {/if}
              {/each}
            {/each}
            {#if tree[m.url] && tree[m.url].entries.length === 0 && !tree[m.url].error}<p class="empty small">Nothing added yet.</p>{/if}
          </div>
        {/each}
      </div>
      <div class="navfoot">
        <button class="btn" disabled={!machines.length} onclick={openAdd}>+ Add</button>
        <button class="btn" disabled={!machines.length} onclick={() => (dialog = 'watches')}>Watches</button>
      </div>
    </nav>

    <main class="term">
      {#if selected}
        {#if selected.sub}
        <div class="termhead mono">
          <span>subagent · {selected.sub.description}{selected.sub.type ? ` (${selected.sub.type})` : ''} · {subRunning(selected.entry, selected.sub) ? 'running' : selected.sub.stopped ? 'stopped' : 'finished'}</span>
          <span class="grow"></span>
          <button class="tiny" onclick={() => open(selected!.machine, selected!.entry)}>← {selected.entry.name}</button>
        </div>
        {:else}
        <div class="termhead mono">
          <span>{selected.machine.name}:{selected.entry.session?.cwd ?? '?'}</span>
          {#if selected.entry.session?.git_branch}<span>⎇ {selected.entry.session.git_branch}</span>{/if}
          <span class="grow"></span>
          <button class="tiny" class:on={termFor === selected.entry.id} onclick={() => { termFor = termFor === selected!.entry.id ? null : selected!.entry.id; if (!termFor) open(selected!.machine, selected!.entry); }}
            title="Switch between the chat view and the real claude terminal">{termFor === selected.entry.id ? 'Chat' : 'Terminal'}</button>
          {#if LIVE_STATES.includes(selected.entry.state)}<button class="tiny" onclick={() => act((l, id) => l.stop(id))} title="Close the live session; it resumes on your next message">Stop</button>{/if}
          <button class="tiny" onclick={togglePin}>{selected.entry.pinned ? 'Unpin' : 'Pin'}</button>
          <button class="tiny" onclick={remove} title="Takes it off the tree; the session stays on its machine">Remove from tree</button>
        </div>
        {/if}
        {#if termFor === selected.entry.id}
          {#key selected.entry.id}<TerminalView live={live()!} entry={selected.entry.id} listen={(fn) => (termSink = fn)} />{/key}
        {:else}
        <!-- focusable so keyboard users can scroll it (a scrollable region should be); the listeners only notice scrolling -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div class="log mono" role="log" aria-label="Session history" bind:this={logEl} onscroll={onLogScroll}
          onwheel={markUser} ontouchmove={markUser} onpointerdown={markUser} onkeydown={markUser} tabindex="0">
          <div class="logbody" bind:this={logBody}>
          {#if loadingHistory}<div class="muted">Loading…</div>{/if}
          {#snippet lines(msg: HistoryMessage)}
            {#each api.render(msg) as line, li}
              {#if line.kind === 'image'}
                <img class="shot" src={line.text} alt="Attachment" />
              {:else if line.kind === 'mail'}
                <div class="mail" class:out={line.dir === 'out'} role="note" aria-label="{line.dir === 'out' ? 'To' : 'From'} {line.who}">
                  <div class="mailhead">✉ <b>{line.dir === 'out' ? 'To' : 'From'} {line.who}</b></div>
                  <div class="md">{@html md(line.text)}</div>
                </div>
              {:else if line.kind === 'text'}
                {#if msg.type === 'user'}
                  <div class="you"><span class="prompt">❯ </span>{line.text}</div>
                {:else}
                  <div class="reply"><span class="bullet">⏺</span><div class="md">{@html md(line.text)}</div></div>
                {/if}
              {:else if line.kind === 'thinking'}
                <details class="thinking"><summary>Thinking</summary><div class="thinktext">{line.text}</div></details>
              {:else if line.kind === 'tool' && AGENT_TOOLS.includes(line.name)}
                {@const kids = byParent[line.id] ?? []}
                {@const sa = selected?.entry.subagents?.find((x) => x.tool_use_id === line.id)}
                {#if sa && !kids.length}
                  <div><span class="bullet">⏺ </span>Agent: {line.input?.description ?? sa.description} <button class="link small" onclick={() => openSub(selected!.machine, selected!.entry, sa)}>open subagent →</button></div>
                {:else}
                <details class="agent" open={!!expanded['agent' + line.id]} ontoggle={(e) => (expanded['agent' + line.id] = (e.currentTarget as HTMLDetailsElement).open)}>
                  <summary><span class="bullet">⏺ </span>Agent: {line.input?.description ?? line.input?.subagent_type ?? 'subagent'}<span class="muted small"> · {kids.length} step{kids.length === 1 ? '' : 's'}</span>{#if sa} <button class="link small" onclick={(ev) => { ev.preventDefault(); openSub(selected!.machine, selected!.entry, sa); }}>open subagent →</button>{/if}</summary>
                  <div class="agentbody">{#each kids as k (k.uuid)}{@render lines(k)}{/each}</div>
                </details>
                {/if}
              {:else if line.kind === 'tool'}
                <div><span class="bullet">⏺ </span><span class="tool">{line.text}</span></div>
              {:else if line.kind === 'diff'}
                <div class="diff" aria-label="{line.text}">
                  <div class="diffhead"><span class="bullet">⏺ </span>{line.text}{#if line.note}<span class="muted small"> · {line.note}</span>{/if}</div>
                  {#each expanded[msg.uuid + li] ? line.lines : line.lines.slice(0, 40) as d}<div class="dl {d.t === '+' ? 'add' : d.t === '-' ? 'del' : ''}"><span class="sign">{d.t}</span>{d.s}</div>{/each}
                  {#if line.lines.length > 40}<button class="link small" onclick={() => (expanded[msg.uuid + li] = !expanded[msg.uuid + li])}>{expanded[msg.uuid + li] ? 'Show less' : `Show all ${line.lines.length} lines`}</button>{/if}
                </div>
              {:else if line.kind === 'todos'}
                <ul class="todos" aria-label="To-dos">
                  {#each line.items as t}<li class={t.status}><span class="box">{t.status === 'completed' ? '✓' : t.status === 'in_progress' ? '▸' : ''}</span>{t.status === 'in_progress' && t.activeForm ? t.activeForm : t.content}</li>{/each}
                </ul>
              {:else}
                {@const rows = line.text.split('\n')}
                <div class="result">⎿ {expanded[msg.uuid + li] ? line.text : rows.slice(0, RESULT_LINES).join('\n')}</div>
                {#if rows.length > RESULT_LINES}<button class="link small more" onclick={() => (expanded[msg.uuid + li] = !expanded[msg.uuid + li])}>{expanded[msg.uuid + li] ? 'Show less' : `Show all ${rows.length} lines`}</button>{/if}
              {/if}
            {/each}
          {/snippet}
          {#each mainMsgs as msg (msg.uuid)}{@render lines(msg)}{/each}
          {#if !selected.sub}
          {#if thinkingLive[selected.entry.id] && !streaming[selected.entry.id]}<div class="thinking live"><span class="muted">Thinking… </span><span class="thinktext">{thinkingLive[selected.entry.id].slice(-240)}</span></div>{/if}
          {#if streaming[selected.entry.id]}<div class="streaming"><span class="bullet">⏺ </span>{streaming[selected.entry.id]}<span class="cursor">▌</span></div>{/if}
          {#each prompts[selected.entry.id] ?? [] as p (p.request_id)}
            <PromptCard {p} live={live()!} />
          {/each}
          {/if}
          </div>
        </div>
        {#if selected.sub}
          <form class="inputbar mono" onsubmit={sendSub}>
            <span class="prompt">❯</span>
            <label class="grow"><span class="sr">Message this subagent</span>
              <textarea rows="1" {@attach grow} bind:value={draft} onkeydown={enterSends} placeholder="Message {selected.sub.description}"></textarea></label>
          </form>
          <div class="subnote muted small">Goes through <button class="link" onclick={() => open(selected!.machine, selected!.entry)}>{selected.entry.name}</button>, which passes it on with SendMessage (resuming the subagent if it has finished).</div>
        {:else}
        {#if liveErr}<div class="liveerr" role="alert">{liveErr}</div>{/if}
        {#if toast}<div class="toast muted small" role="status">{toast}</div>{/if}
        {#if pendingQ[selected.entry.id]?.length}
          <div class="pendingq mono" role="list" aria-label="Queued messages">
            {#each pendingQ[selected.entry.id] as m (m.uuid)}<div class="you" role="listitem"><span class="prompt">❯ </span>{msgText(m)}</div>{/each}
          </div>
        {/if}
        {#if queued[selected.entry.id]}<div class="queued muted small" aria-live="polite">{queued[selected.entry.id]} queued: sends when Claude finishes (Esc puts {queued[selected.entry.id] === 1 ? 'it' : 'them'} back here)</div>{/if}
        {#if menu.length}
          <div class="cmdmenu" role="listbox" aria-label="Slash commands">
            {#each menu as c, i (c.name)}
              <button type="button" role="option" aria-selected={i === cmdIdx} class:on={i === cmdIdx} onclick={() => complete(c)}>
                <span class="mono">/{c.name}</span><span class="muted small">{c.description}</span>
              </button>
            {/each}
          </div>
        {/if}
        {#if attachments.length}
          <div class="attach" aria-label="Attached images">
            {#each attachments as a, i (a.url)}
              <span class="thumb"><img src={a.url} alt={a.name} /><button type="button" aria-label="Remove {a.name}" onclick={() => (attachments = attachments.filter((_, j) => j !== i))}>×</button></span>
            {/each}
          </div>
        {/if}
        <form class="inputbar mono" onsubmit={sendDraft} ondragover={(e) => e.preventDefault()} ondrop={onDrop}>
          <span class="prompt">❯</span>
          <label class="grow"><span class="sr">Message this session</span>
            <textarea rows="1" {@attach grow} bind:value={draft} onpaste={onPaste} onkeydown={onInputKey} disabled={selected.entry.state === 'terminal' || selected.entry.state === 'missing'}
              placeholder={selected.entry.state === 'terminal' ? 'Open in a terminal elsewhere' : selected.entry.state === 'working' ? 'Working… Enter queues, Ctrl+Enter interrupts and sends (Esc to interrupt)' : 'Message this session (Shift+Enter for a new line)'}></textarea></label>
          <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden bind:this={fileInput} onchange={(e) => { addFiles((e.target as HTMLInputElement).files ?? []); (e.target as HTMLInputElement).value = ''; }} />
          <button type="button" class="tiny" aria-label="Attach images" title="Attach images (or paste / drop them)" onclick={() => fileInput?.click()}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" /></svg>
          </button>
          {#if selected.entry.state === 'working'}<button type="button" class="tiny" onclick={() => act((l, id) => l.interrupt(id))}>Esc · interrupt</button>{/if}
        </form>
        {/if}
        {/if}
      {:else}
        <div class="placeholder">Pick a session from the tree, or <button class="link" disabled={!machines.length} onclick={openAdd}>add one</button>.</div>
      {/if}
    </main>
    {#if showInbox && machines.length}
      <InboxView items={inboxItems} onOpen={openFromInbox} onRead={readItem} onDelete={deleteItem} onReadAll={readAll} onClose={() => (showInbox = false)} />
    {/if}
  </div>

  <footer class="status mono">
    <span>{machines.length} machine{machines.length === 1 ? '' : 's'}</span>
    {#if selected}
      <label class="modepick">account
        <select aria-label="Account" value={selected.entry.account ?? ''} onchange={(ev) => act((l, id) => { const v = (ev.target as HTMLSelectElement).value; l.setAccount(id, v || null); })}
          title="Switching closes the live session; your next message resumes it on the new account">
          <option value="">machine login</option>
          {#each acctList[selected.machine.url] ?? [] as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
        </select>
      </label>
      <label class="modepick">model
        <select aria-label="Model" value={selected.entry.model ?? 'default'} onchange={(ev) => act((l, id) => { const v = (ev.target as HTMLSelectElement).value; l.setModel(id, v === 'default' ? null : v); })}>
          {#each modelList[selected.machine.url]?.length ? modelList[selected.machine.url] : [{ value: 'default', displayName: 'Default' }] as m (m.value)}
            <option value={m.value} title={m.description ?? ''}>{m.displayName}</option>
          {/each}
        </select>
      </label>
      {@const mdl = (modelList[selected.machine.url] ?? []).find((m) => m.value === (selected!.entry.model ?? 'default'))}
      {#if mdl?.supportsEffort && mdl.supportedEffortLevels?.length}
        <label class="modepick">effort
          <select aria-label="Effort" value={selected.entry.effort ?? ''} title="Applies from the next message (the live session restarts)"
            onchange={(ev) => act((l, id) => l.setEffort(id, (ev.target as HTMLSelectElement).value || null))}>
            <option value="">default</option>
            {#each mdl.supportedEffortLevels as lvl}<option value={lvl}>{lvl}</option>{/each}
          </select>
        </label>
      {/if}
      <label class="modepick">mode
        <select aria-label="Permission mode" value={selected.entry.mode ?? 'default'} onchange={(ev) => act((l, id) => l.setMode(id, (ev.target as HTMLSelectElement).value))}>
          {#each api.MODES as m}<option value={m}>{m}</option>{/each}
        </select>
      </label>
    {/if}
    <span class="grow"></span>
    {#if selected?.entry.usage}
      {@const u = selected.entry.usage}
      <span title="Context used by the latest request{u.context_window ? ` of a ${Math.round(u.context_window / 1000)}k window` : ''}; cost is the API-equivalent for this session (plans aren't billed per token)">
        ctx {u.context_window ? `${Math.round((u.context_used / u.context_window) * 100)}%` : `${Math.round(u.context_used / 1000)}k`} · ≈${u.session_cost.toFixed(2)}
      </span>
    {/if}
    <span>{counts.working ?? 0} working · {counts.waiting ?? 0} waiting · {counts.ready ?? 0} live · {counts.stopped ?? 0} stopped · {counts.terminal ?? 0} in terminal</span>
  </footer>
</div>

{#if dialog === 'connect'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <form class="dialog" aria-label="Connect a machine" onsubmit={connect}>
      <h2>Connect a machine</h2>
      <p class="muted">On that machine run <code>switchboard-daemon pair</code> and enter the code it prints.</p>
      <label>Daemon address<input bind:value={cUrl} required /></label>
      <label>One-time code<input bind:value={cCode} required autocomplete="off" placeholder="e.g. K7QM2XRA" use:focusOnMount /></label>
      {#if cError}<p class="error">{cError}</p>{/if}
      <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button><button class="btn primary">Connect</button></div>
    </form>
  </div>
{/if}

{#if dialog === 'watches'}
  <WatchesView {machines} entries={Object.fromEntries(Object.entries(tree).map(([u, t]) => [u, t.entries]))} onClose={() => (dialog = null)} />
{/if}

{#if dialog === 'report'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <form class="dialog" aria-label="Report a bug" onsubmit={sendReport}>
      <h2>Report a bug</h2>
      <label>What happened, and what did you expect?
        <textarea bind:value={rDesc} required rows="6" placeholder="First line becomes the title"></textarea>
      </label>
      <label class="inline"><input type="checkbox" bind:checked={rLogs} /> Include recent app and daemon logs (tokens are removed)</label>
      {#if rDone}<p class="muted" role="status">{rDone}</p>{/if}
      {#if rReport}<details><summary class="muted small">Report</summary><textarea class="mono small" readonly rows="10">{rReport}</textarea></details>{/if}
      <div class="actions">
        <button type="button" class="btn ghost" onclick={() => (dialog = null)}>Close</button>
        <button class="btn primary" disabled={rBusy || !rDesc.trim()}>{rBusy ? 'Collecting…' : 'Copy report & open GitHub'}</button>
      </div>
    </form>
  </div>
{/if}

{#if dialog === 'settings'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <div class="dialog" role="dialog" aria-label="Settings">
      <h2>Settings</h2>
      <label>Theme<select value={theme.name} onchange={(e) => applyTheme((e.currentTarget as HTMLSelectElement).value)}>
        {#each Object.entries(THEMES) as [k, t]}<option value={k}>{t.label}</option>{/each}</select></label>
      <div class="grid3">
        <label>Text size<select value={fonts.size} onchange={(e) => applyFonts({ size: +(e.currentTarget as HTMLSelectElement).value })}>
          {#each SIZES as z}<option value={z}>{z}%</option>{/each}</select></label>
        <label>Interface font<select value={fonts.ui} onchange={(e) => applyFonts({ ui: (e.currentTarget as HTMLSelectElement).value })}>
          {#each Object.entries(UI_FONTS) as [k, f]}<option value={k}>{f.label}</option>{/each}</select></label>
        <label>Chat and code font<select value={fonts.mono} onchange={(e) => applyFonts({ mono: (e.currentTarget as HTMLSelectElement).value })}>
          {#each Object.entries(MONO_FONTS) as [k, f]}<option value={k}>{f.label}</option>{/each}</select></label>
      </div>
      {#if !inShell()}
        <p class="muted">Settings for this machine are available in the desktop app.</p>
      {:else if svc && !svc.supported}
        <p class="muted">Keeping agents running in the background is Linux-only for now.</p>
      {:else if svc}
        <label class="toggle">
          <input type="checkbox" checked={svc.installed} disabled={svcBusy} onchange={toggleService} />
          <span><b>Keep agents running when Switchboard is closed</b><br />
            <span class="muted small">Runs this machine's daemon as a background service (systemd user service) instead of inside the app.</span></span>
        </label>
        <p class="muted small" aria-live="polite">
          {svcBusy ? 'Switching…' : svc.installed ? (svc.active ? 'Background service: running' : 'Background service: installed but not running') : svc.embedded ? "Daemon: running inside the app" : 'Daemon: running elsewhere on this machine'}
        </p>
      {/if}
      {#if svcError}<p class="error">{svcError}</p>{/if}

      <h3 class="sub">Other devices</h3>
      {#if !localMachine}
        <p class="muted small">Available for the machine this app runs on.</p>
      {:else if net}
        <label class="toggle">
          <input type="checkbox" checked={net.listen_tailscale} disabled={netBusy || (!net.tailscale && !net.listen_tailscale)} onchange={toggleNet} />
          <span><b>Reachable from my other devices over Tailscale</b><br />
            <span class="muted small">{net.tailscale ? `This machine is ${net.tailscale.dns ?? net.tailscale.ips[0]} on your tailnet. Only your Tailscale devices can reach it, and each still has to be paired.` : "Tailscale isn't running on this machine."}</span></span>
        </label>
        {#if net.listen_tailscale && net.active.length}
          <p class="small" aria-live="polite">Reachable at <code>{net.url ?? `http://${net.active[0]}`}</code>{net.url ? ` (or http://${net.active[0]})` : ''}</p>
          <div class="actions" style="justify-content: flex-start"><button type="button" class="btn ghost" onclick={newCode}>Pair another device</button></div>
          {#if code && code.until > now}
            <div class="paircode" role="status">
              <div>On the other device, open Switchboard → <b>+ Connect a machine</b>:</div>
              <div>Address <code>{net.url ?? `http://${net.active[0]}`}</code> · code <b class="mono big">{code.code}</b></div>
              <div class="muted small">Valid for {Math.max(0, Math.round((code.until - now) / 1000 / 60))} more min, once.</div>
            </div>
          {:else if code}<p class="muted small">That code expired. <button class="link" onclick={newCode}>New code</button></p>{/if}
        {/if}
        {#if net.error}<p class="error">{net.error}</p>{/if}
      {/if}
      {#if netError}<p class="error">{netError === 'only_from_this_machine' ? 'This can only be changed on that machine itself.' : netError}</p>{/if}

      <h3 class="sub">Updates</h3>
      <p class="muted small">Switchboard {version || '…'}. Updates come from github.com/cyaninc-lab/switchboard-releases and are checked for a valid signature before installing.</p>
      <div class="actions" style="justify-content: flex-start"><button type="button" class="btn ghost" onclick={() => checkUpdates(true)}>Check for updates</button></div>
      {#if updMsg}<p class="muted small" role="status">{updMsg}</p>{/if}

      <h3 class="sub">Accounts</h3>
      {#if machines.length}
        <div class="pills">
          {#each machines as m (m.url)}
            <button type="button" class="pill" class:on={accMachine?.url === m.url} onclick={() => { accMachine = m; loadAccounts(m); }}>{m.name}</button>
          {/each}
        </div>
        <ul class="acclist" aria-label="Accounts on {accMachine?.name}">
          <li><span>machine login</span><span class="muted small">whoever Claude Code is signed in as on {accMachine?.name}</span></li>
          {#each accMachine ? acctList[accMachine.url] ?? [] : [] as a (a.name)}
            <li><span>{a.name}</span><span class="muted small">{a.kind === 'api_key' ? 'API key' : 'setup-token'}</span>
              <button type="button" class="tiny" aria-label="Remove account {a.name}" onclick={() => removeAccount(a.name)}>Remove</button></li>
          {/each}
        </ul>
        <form class="accform" onsubmit={addAccount} aria-label="Add an account">
          <label>Name<input bind:value={accName} placeholder="e.g. work" /></label>
          <label>Token <span class="muted small">(run <code>claude setup-token</code> signed in to that account)</span>
            <input type="password" bind:value={accToken} autocomplete="off" placeholder="sk-ant-oat…" /></label>
          <label class="inline"><input type="checkbox" checked={accKind === 'api_key'} onchange={(e) => (accKind = (e.target as HTMLInputElement).checked ? 'api_key' : 'oauth')} /> It's an Anthropic API key</label>
          {#if accError}<p class="error">{accError}</p>{/if}
          <div class="actions"><button class="btn primary">Add account</button></div>
        </form>
      {:else}
        <p class="muted">Connect a machine first.</p>
      {/if}
      <div class="actions"><button class="btn ghost" onclick={() => (dialog = null)}>Close</button></div>
    </div>
  </div>
{/if}

{#if dialog === 'add'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <form class="dialog wide" aria-label="Add to Switchboard" onsubmit={(e) => (aTab === 'new' ? createNew(e) : add(e))}>
      <h2>Add to Switchboard</h2>
      <div class="tabs" role="tablist" aria-label="Add from">
        <button type="button" role="tab" aria-selected={aTab === 'existing'} class:on={aTab === 'existing'} onclick={() => (aTab = 'existing')}>Existing session</button>
        <button type="button" role="tab" aria-selected={aTab === 'new'} class:on={aTab === 'new'} onclick={newTab}>New session</button>
      </div>
      <div class="pills">
        <span class="muted small">Machine</span>
        {#each machines as m (m.url)}
          <button type="button" class="pill" class:on={aMachine?.url === m.url} onclick={() => { aMachine = m; aPicks = []; aTab === 'new' ? newTab() : search(); }}>{m.name}</button>
        {/each}
      </div>
      {#snippet projectPicker()}
        <label>Project<select bind:value={aProj}>
          <option value="">Project folder (default)</option>
          {#each projects as g (g)}<option value={g}>{g}</option>{/each}
          <option value={'\u0000new'}>New project…</option>
        </select></label>
        {#if aProj === '\u0000new'}<label>New project name<input bind:value={aNewProj} required placeholder="e.g. Client work" /></label>{/if}
      {/snippet}
      {#if aTab === 'new'}
        <label>Folder on {aMachine?.name}
          <input bind:value={nFolder} oninput={() => (nTyped = true)} onfocus={() => (nTyped = true)} list="folder-list" required placeholder="/path/to/project" />
          <datalist id="folder-list">{#each nFolders as f}<option value={f}></option>{/each}</datalist>
        </label>
        <div class="grid2">
          <label>Name in the tree<input bind:value={nName} placeholder="New session" /></label>
          {@render projectPicker()}
          <label>Permission mode<select bind:value={nMode}>{#each api.MODES as m}<option value={m}>{m}</option>{/each}</select></label>
          <label>Account<select bind:value={nAccount}>
            <option value="">machine login</option>
            {#each aMachine ? acctList[aMachine.url] ?? [] : [] as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
          </select></label>
          <label>Model<select bind:value={nModel}>
            {#each (aMachine && modelList[aMachine.url]?.length) ? modelList[aMachine.url] : [{ value: 'default', displayName: 'Default' }] as m (m.value)}<option value={m.value}>{m.displayName}</option>{/each}
          </select></label>
        </div>
        {#if aError}<p class="error">{aError}</p>{/if}
        <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button><button class="btn primary">Create session</button></div>
      {:else}
      <label class="search"><span class="sr">Search</span><input type="search" placeholder="Search sessions on {aMachine?.name}…" bind:value={aQuery} oninput={search} /></label>
      <div class="slist" role="group" aria-label="Sessions">
        {#each aSessions as s (s.session_id)}
          <button type="button" class="srow" class:on={aPicks.some((x) => x.session_id === s.session_id)} aria-pressed={aPicks.some((x) => x.session_id === s.session_id)} class:intree={!!s.entry_id} disabled={!!s.entry_id} onclick={() => pick(s)}>
            <span class="stitle">{s.custom_title || s.summary || 'Untitled'}{#if s.entry_id}<span class="badge">in tree</span>{/if}</span>
            <span class="mono muted small ellip">{s.cwd}{s.git_branch ? ` · ${s.git_branch}` : ''}</span>
            <span class="muted small">{ago(s.last_modified)}</span>
          </button>
        {/each}
        <p class="muted small">Showing {aSessions.length} of {aTotal} · click to pick, click more to add several</p>
      </div>
      {#if aPicks.length}
        <div class="grid2">
          {#if aPick}<label>Name in the tree<input bind:value={aName} required /></label>
          {:else}<p class="muted small">{aPicks.length} sessions, each named after its title</p>{/if}
          {@render projectPicker()}
        </div>
      {/if}
      {#if aError}<p class="error">{aError}</p>{/if}
      <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button><button class="btn primary" disabled={!aPicks.length}>{aPicks.length > 1 ? `Add ${aPicks.length} to tree` : 'Add to tree'}</button></div>
      {/if}
    </form>
  </div>
{/if}

<style>
  /* Catppuccin Mocha */
  :global(:root) {
    --crust: #11111b; --mantle: #181825; --base: #1e1e2e; --s0: #313244; --s1: #45475a; --s2: #585b70;
    --o1: #7f849c; --o2: #9399b2; --sub0: #a6adc8; --sub1: #bac2de; --text: #cdd6f4;
    --mauve: #cba6f7; --green: #a6e3a1; --peach: #fab387; --sky: #89dceb; --teal: #94e2d5; --red: #f38ba8;
  }
  :global(html, body) { margin: 0; height: 100%; background: var(--crust); color: var(--text); font: 13px/1.45 var(--ui-font, Inter, system-ui, sans-serif); }
  :global(button) { font: inherit; cursor: pointer; }
  :global(.mono) { font-family: var(--mono-font, 'JetBrains Mono', ui-monospace, monospace); }
  :global(.muted) { color: var(--o2); } .small { font-size: 11px; } .grow { flex: 1; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  code { background: var(--s0); padding: 1px 5px; border-radius: 4px; font-size: 12px; }

  .app { display: flex; flex-direction: column; height: 100vh; background: var(--base); }
  .titlebar { display: flex; align-items: center; gap: 16px; padding: 10px 16px; background: var(--mantle); border-bottom: 1px solid var(--s0); flex-wrap: wrap; }
  .brand { display: flex; gap: 8px; align-items: center; font-weight: 600; font-size: 14px; }
  .chips { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: var(--s0); border: 1px solid var(--s1); color: var(--text); }
  .chip.icon { padding: 4px 8px; cursor: pointer; }
  .chip.hot { border-color: var(--mauve); }
  .count { background: var(--mauve); color: var(--crust); border-radius: 999px; padding: 0 6px; font-size: 11px; font-weight: 600; }
  .navfoot { display: flex; gap: 8px; }
  .toggle { display: flex !important; flex-direction: row !important; gap: 10px; align-items: flex-start; color: var(--text) !important; font-size: 13px !important; }
  .toggle input { width: auto; margin-top: 3px; }
  .chip.dashed { background: none; border-style: dashed; border-color: var(--s2); color: var(--sub0); }
  .dot { width: 8px; height: 8px; border-radius: 50%; box-sizing: border-box; }
  .dot.on { background: var(--green); } .dot.off { border: 1.5px solid var(--o1); }

  .body { flex: 1; display: flex; min-height: 0; }
  .tree { width: 290px; flex: 0 0 290px; display: flex; flex-direction: column; background: var(--mantle); border-right: 1px solid var(--s0); }
  .search { display: block; padding: 12px; }
  :global(input:focus), :global(select:focus) { outline: 1px solid var(--mauve); outline-offset: 0; }
  :global(input) { width: 100%; box-sizing: border-box; background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; }
  .scroll { flex: 1; overflow: auto; padding: 0 8px 12px; }
  .empty { color: var(--o2); padding: 8px; }
  .machine { margin-top: 6px; } .machine.offline { opacity: 0.6; }
  .mhead { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
  .mname { font-weight: 600; flex: 1; }
  :global(.tiny.on) { border-color: var(--mauve); color: var(--text); }
  :global(.tiny) { background: none; border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 2px 8px; font-size: 12px; }
  .group { display: block; width: 100%; text-align: left; border: 0; background: none; cursor: pointer; padding: 6px 8px 2px 12px; color: var(--sub0); font: inherit; }
  .group:hover { color: var(--text); }
  .st.inline { display: inline-grid; vertical-align: middle; margin-left: 6px; }
  .entry { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: 0; border-radius: 8px; padding: 6px 8px 6px 36px; background: none; color: var(--sub1); }
  .entry:hover { background: var(--base); }
  .entry.sel { background: var(--s0); color: var(--text); box-shadow: inset 2px 0 0 var(--mauve); }
  .entry.sub { font-size: 12.5px; padding-top: 4px; padding-bottom: 4px; }
  .subtoggle { display: block; border: 0; background: none; color: var(--o1); padding: 2px 8px 2px 50px; cursor: pointer; text-align: left; }
  .subtoggle:hover { color: var(--text); }
  .subnote { padding: 0 16px 8px; }
  .entry.waiting { font-weight: 600; }
  .ename { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pin { color: var(--peach); font-size: 11px; }
  .ctxmenu { position: fixed; z-index: 50; min-width: 160px; padding: 4px; background: var(--mantle); border: 1px solid var(--s1); border-radius: 8px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4); }
  .ctxmenu button { display: block; width: 100%; text-align: left; border: 0; border-radius: 6px; padding: 6px 10px; background: none; color: var(--text); }
  .ctxmenu button:hover:not(:disabled) { background: var(--s0); }
  .ctxmenu button:disabled { color: var(--o1); }
  input.rename { padding: 2px 6px; font-size: inherit; border-radius: 6px; }
  .st { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; box-sizing: border-box; border: 1.5px solid var(--o1); }
  .st.ready { border: 2px solid var(--green); } .st.unread { background: var(--green); border: 0; }
  .st.working { border: 2px solid var(--green); border-top-color: transparent; animation: spin 0.8s linear infinite; }
  .st.waiting { border: 0; border-radius: 0; width: 12px; height: 12px; margin: -2px; display: grid; place-items: center; font-size: 12px; line-height: 1; color: var(--green); animation: blink 1s step-end infinite; }
  .st.waiting::before { content: '☀'; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .st.working, .st.waiting { animation: none; } }
  .st.new { border: 1.5px dashed var(--mauve); }
  .st.terminal { background: var(--sky); border: 0; border-radius: 2px; } .st.missing, .st.error { border-color: var(--red); }
  .navfoot { padding: 10px 12px; border-top: 1px solid var(--s0); }
  :global(.btn) { width: 100%; background: var(--s0); border: 1px solid var(--s1); color: var(--text); border-radius: 8px; padding: 9px; }
  :global(.btn:disabled) { opacity: 0.5; cursor: default; }
  :global(.btn.primary) { background: var(--mauve); color: var(--crust); border: 0; font-weight: 600; width: auto; padding: 9px 18px; }
  :global(.btn.ghost) { width: auto; padding: 9px 16px; }

  .term { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--base); }
  .termhead { display: flex; gap: 16px; align-items: center; padding: 8px 18px; font-size: 12px; color: var(--o2); border-bottom: 1px solid var(--s0); }
  .logbody { display: flex; flex-direction: column; gap: 8px; }
  .log { flex: 1; overflow: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  /* your messages: a soft mauve band with an edge, so they stand out when scrolling back */
  .you { color: var(--text); background: color-mix(in srgb, var(--mauve) 10%, transparent); border-left: 3px solid var(--mauve); border-radius: 6px; padding: 8px 12px; margin: 4px 0; }
  .reply { display: flex; gap: 8px; white-space: normal; }
  /* messages between sessions: their own box, sky for received, teal for sent */
  .mail { border: 1px solid var(--s1); border-left: 3px solid var(--sky); border-radius: 8px; background: rgba(137, 220, 235, 0.06); padding: 8px 12px; white-space: normal; }
  .mail.out { border-left-color: var(--teal); background: rgba(148, 226, 213, 0.05); }
  .mailhead { font: 12px var(--ui-font, Inter, system-ui, sans-serif); color: var(--sky); margin-bottom: 4px; } .mail.out .mailhead { color: var(--teal); }
  .mailhead b { font-weight: 600; }
  .md { min-width: 0; font-family: var(--ui-font, Inter, system-ui, sans-serif); line-height: 1.55; }
  .md :global(p) { margin: 0 0 8px; } .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-size: 14px; margin: 10px 0 6px; color: var(--mauve); }
  .md :global(code) { font-family: var(--mono-font, 'JetBrains Mono', monospace); background: var(--s0); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .md :global(pre) { background: var(--mantle); border: 1px solid var(--s0); border-radius: 8px; padding: 10px 12px; overflow-x: auto; }
  .md :global(pre code) { background: none; padding: 0; }
  .md :global(table) { border-collapse: collapse; margin: 6px 0; font-size: 12px; display: block; overflow-x: auto; }
  .md :global(th), .md :global(td) { border: 1px solid var(--s1); padding: 3px 8px; text-align: left; }
  .md :global(th) { background: var(--s0); }
  .md :global(a) { color: var(--sky); }
  .md :global(ul), .md :global(ol) { margin: 4px 0 8px; padding-left: 20px; } .prompt { color: var(--mauve); font-weight: 600; } .bullet { color: var(--mauve); }
  .tool { color: var(--sub1); } .result { color: var(--o2); padding-left: 18px; }
  .more { align-self: flex-start; margin-left: 30px; }
  .thinking { color: var(--o2); font-style: italic; white-space: pre-wrap; } .thinking summary { cursor: pointer; font-style: normal; }
  .thinking.live .thinktext { opacity: 0.8; }
  .thinktext { display: block; padding: 4px 0 4px 18px; font-size: 12px; }
  .agent { border-left: 2px solid var(--s1); padding-left: 10px; } .agent summary { cursor: pointer; color: var(--sub1); }
  .agentbody { display: flex; flex-direction: column; gap: 6px; padding-top: 6px; }
  .diff { display: flex; flex-direction: column; border: 1px solid var(--s0); border-radius: 8px; overflow: hidden; }
  .diffhead { padding: 4px 10px; background: var(--mantle); color: var(--sub1); }
  .dl { padding: 0 10px; white-space: pre-wrap; font-size: 12px; } .dl .sign { display: inline-block; width: 14px; color: var(--o1); }
  .dl.add { background: color-mix(in srgb, var(--green) 12%, transparent); color: var(--green); } .dl.del { background: color-mix(in srgb, var(--red) 12%, transparent); color: var(--red); }
  .diff .link { align-self: flex-start; margin: 4px 10px; }
  .todos { list-style: none; margin: 0; padding: 6px 10px; border: 1px solid var(--s0); border-radius: 8px; display: flex; flex-direction: column; gap: 2px; font-family: var(--ui-font, Inter, system-ui, sans-serif); white-space: normal; }
  .todos .box { display: inline-block; width: 14px; height: 14px; border: 1px solid var(--s2); border-radius: 3px; margin-right: 8px; text-align: center; line-height: 13px; font-size: 11px; vertical-align: -2px; }
  .todos li.completed { color: var(--o2); text-decoration: line-through; } .todos li.completed .box { color: var(--green); border-color: var(--green); }
  .todos li.in_progress { color: var(--text); font-weight: 600; } .todos li.in_progress .box { color: var(--peach); border-color: var(--peach); }
  .inputbar { display: flex; gap: 10px; align-items: center; padding: 12px 18px; border-top: 1px solid var(--s0); }
  .inputbar { align-items: flex-end; }
  .toast { padding: 4px 18px 0; word-break: break-all; }
  .md :global(code.path) { cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
  .md :global(code.path:hover) { color: var(--sky); }
  .pendingq { padding: 8px 18px 0; display: flex; flex-direction: column; gap: 4px; opacity: 0.75; white-space: pre-wrap; max-height: 25vh; overflow-y: auto; }
  .inputbar textarea { display: block; width: 100%; box-sizing: border-box; border: 0; background: none; color: var(--text); font: inherit; padding: 8px 10px; resize: none; max-height: 40vh; overflow-y: auto; line-height: 1.45; }
  .inputbar textarea:focus { outline: 1px solid var(--mauve); border-radius: 8px; }
  .inputbar .prompt { padding-bottom: 8px; }
  .placeholder { margin: auto; color: var(--o2); }
  .streaming { color: var(--text); } .cursor { color: var(--mauve); animation: blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .perm { border: 1px solid color-mix(in srgb, var(--peach) 45%, transparent); background: color-mix(in srgb, var(--peach) 8%, transparent); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; white-space: normal; }
  .permtitle { color: var(--peach); font-weight: 600; }
  .permactions { display: flex; gap: 8px; font-family: var(--ui-font, Inter, system-ui, sans-serif); }
  .allow { background: var(--green); color: var(--crust); border: 0; border-radius: 8px; padding: 8px 16px; font-weight: 600; }
  .deny { background: none; color: var(--red); border: 1px solid color-mix(in srgb, var(--red) 45%, transparent); border-radius: 8px; padding: 8px 16px; }
  .shot { max-width: 320px; max-height: 200px; border-radius: 8px; border: 1px solid var(--s1); display: block; margin: 4px 0 4px 18px; }
  .cmdmenu { margin: 0 18px; background: var(--mantle); border: 1px solid var(--s1); border-radius: 10px; padding: 4px; display: flex; flex-direction: column; }
  .cmdmenu button { display: grid; grid-template-columns: minmax(140px, auto) 1fr; gap: 12px; text-align: left; background: none; border: 0; color: var(--text); padding: 6px 10px; border-radius: 6px; }
  .cmdmenu button.on { background: var(--s0); }
  .cmdmenu .small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .attach { display: flex; gap: 8px; padding: 8px 18px 0; flex-wrap: wrap; }
  .thumb { position: relative; } .thumb img { height: 56px; border-radius: 6px; border: 1px solid var(--s1); display: block; }
  .thumb button { position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; border-radius: 50%; border: 0; background: var(--s1); color: var(--text); font-size: 12px; line-height: 18px; padding: 0; }
  .queued { padding: 4px 18px 0; }
  .liveerr { color: var(--red); padding: 6px 18px; font-size: 12px; }
  .modepick { display: inline-flex; gap: 6px; align-items: center; }
  .modepick select { appearance: none; -webkit-appearance: none; padding-right: 14px; background: var(--base); color: var(--text); border: 1px solid var(--s1); border-radius: 6px; font: inherit; padding: 1px 4px; }
  .link { background: none; border: 0; color: var(--sky); padding: 0; text-decoration: underline; }

  .status { display: flex; gap: 18px; padding: 6px 16px; background: var(--mantle); border-top: 1px solid var(--s0); font-size: 11px; color: var(--o2); }

  :global(.backdrop) { position: fixed; inset: 0; background: color-mix(in srgb, var(--crust) 70%, transparent); display: flex; align-items: center; justify-content: center; padding: 16px; }
  :global(.dialog) { width: 100%; max-width: 440px; background: var(--base); border: 1px solid var(--s1); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  :global(.dialog.wide) { max-width: 780px; max-height: 90vh; overflow: auto; }
  :global(.dialog h2) { margin: 0; font-size: 16px; }
  .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  :global(.dialog label) { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--sub0); }
  :global(.dialog .search) { padding: 0; }
  :global(.actions) { display: flex; justify-content: flex-end; gap: 8px; }
  :global(.error) { color: var(--red); margin: 0; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--s0); }
  .tabs button { background: none; border: 0; border-bottom: 2px solid transparent; color: var(--sub0); padding: 6px 12px; }
  .tabs button.on { border-bottom-color: var(--mauve); color: var(--text); font-weight: 600; }
  :global(.dialog select) { background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; appearance: none; }
  .sub { margin: 8px 0 0; font-size: 13px; }
  .paircode { border: 1px solid color-mix(in srgb, var(--mauve) 45%, transparent); background: color-mix(in srgb, var(--mauve) 7%, transparent); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
  .big { font-size: 16px; letter-spacing: 1px; color: var(--mauve); }
  .updbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 16px; background: color-mix(in srgb, var(--mauve) 12%, transparent); border-bottom: 1px solid color-mix(in srgb, var(--mauve) 35%, transparent); }
  .updnotes { padding: 8px 16px 12px; background: var(--mantle); border-bottom: 1px solid var(--s0); max-height: 200px; overflow: auto; }
  :global(.btn.small) { padding: 5px 12px; }
  :global(.dialog textarea) { width: 100%; box-sizing: border-box; background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; resize: vertical; }
  .acclist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .acclist li { display: grid; grid-template-columns: 120px 1fr auto; gap: 10px; align-items: center; padding: 6px 10px; background: var(--mantle); border-radius: 8px; }
  .accform { display: flex; flex-direction: column; gap: 8px; }
  :global(.dialog label.inline) { flex-direction: row; align-items: center; gap: 8px; } .dialog label.inline input { width: auto; }
  :global(.dialog) { max-height: 90vh; overflow: auto; }
  :global(.pills) { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  :global(.pill) { background: var(--mantle); border: 1px solid var(--s1); color: var(--sub1); border-radius: 999px; padding: 6px 12px; }
  :global(.pill.on) { background: var(--s0); border-color: var(--mauve); color: var(--text); }
  .slist { display: flex; flex-direction: column; gap: 6px; }
  .srow { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr) 50px; gap: 12px; align-items: center; text-align: left; padding: 10px 12px; border-radius: 10px; background: var(--mantle); border: 1px solid var(--s0); color: var(--text); }
  .srow.on { background: var(--s0); border-color: var(--mauve); }
  .srow.intree { opacity: 0.6; cursor: default; }
  .stitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { margin-left: 8px; font-size: 11px; color: var(--sub0); border: 1px solid var(--s1); border-radius: 999px; padding: 1px 8px; }
  :global(.grid2) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
</style>
