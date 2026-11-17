<script lang="ts">
  import * as api from '$lib/api';
  import { pairLocal, serviceStatus, serviceInstall, serviceUninstall, inShell, openExternal, appVersion, checkForUpdate } from '$lib/shell';
  import type { Update } from '$lib/shell';
  import { captureAppLog, buildReport, issueUrl } from '$lib/report';
  import type { ServiceStatus } from '$lib/shell';
  import TerminalView from '$lib/Terminal.svelte';
  import { md } from '$lib/markdown';
  import { tick } from 'svelte';
  import type { Machine, Entry, Session, HistoryMessage } from '$lib/api';

  // the HTML autofocus attribute only works on page load, not on elements Svelte adds later
  const focusOnMount = (node: HTMLElement) => { node.focus(); };

  // ---- state ----
  let machines = $state<Machine[]>(api.loadMachines());
  let tree = $state<Record<string, { entries: Entry[]; error?: string }>>({});
  let selected = $state<{ machine: Machine; entry: Entry } | null>(null);
  let messages = $state<HistoryMessage[]>([]);
  let loadingHistory = $state(false);
  let openSeq = 0;
  let filter = $state('');
  let dialog = $state<'connect' | 'add' | 'settings' | 'report' | null>(null);
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
  async function openSettings() {
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
  let aPick = $state<Session | null>(null);
  let aName = $state('');
  let aGroup = $state('');
  let aError = $state('');
  let aTab = $state<'existing' | 'new'>('existing');
  let nFolder = $state('');
  let nFolders = $state<string[]>([]);
  let nName = $state('');
  let nMode = $state('default');
  let nModel = $state('default');
  let nAccount = $state('');
  async function newTab() {
    aTab = 'new'; aError = '';
    if (!aMachine) return;
    loadModels(aMachine); loadAccounts(aMachine);
    try { const f = await api.folders(aMachine); nFolders = f.folders; nFolder ||= f.folders[1] ?? f.home; } catch (e) { aError = (e as Error).message; }
  }
  async function createNew(ev: Event) {
    ev.preventDefault();
    if (!aMachine) return;
    try {
      const e = await api.addEntry(aMachine, { new: { cwd: nFolder.trim() }, name: nName.trim() || undefined, mode: nMode, model: nModel === 'default' ? undefined : nModel, account: nAccount || undefined });
      dialog = null; nName = '';
      await refresh();
      open(aMachine, e);
    } catch (e) { aError = (e as Error).message === 'no_such_folder' ? `No folder ${nFolder} on ${aMachine.name}` : (e as Error).message; }
  }

  // ---- live sessions (phase 2) ----
  const lives = new Map<string, api.Live>();
  let streaming = $state<Record<string, string>>({});
  let queued = $state<Record<string, number>>({});
  let prompts = $state<Record<string, { request_id: string; tool: string; input: any }[]>>({});
  let draft = $state('');
  // follow new output while the view is at the bottom; leave it alone once the user scrolls up to read
  let logEl = $state<HTMLDivElement>();
  let stick = true;
  const onLogScroll = () => { if (logEl) stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 80; };
  $effect(() => {
    messages.length; selected && streaming[selected.entry.id]; selected && prompts[selected.entry.id]?.length;
    if (stick) tick().then(() => logEl && (logEl.scrollTop = logEl.scrollHeight));
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
  function onInputKey(ev: KeyboardEvent) {
    if (!menu.length) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); cmdIdx = (cmdIdx + 1) % menu.length; }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); cmdIdx = (cmdIdx - 1 + menu.length) % menu.length; }
    else if (ev.key === 'Tab' || (ev.key === 'Enter' && draft !== `/${menu[cmdIdx]?.name}`)) { ev.preventDefault(); complete(menu[Math.min(cmdIdx, menu.length - 1)]); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); draft = ''; }
  }
  let liveErr = $state('');
  const LIVE_STATES = ['working', 'waiting', 'ready'];
  // real-terminal toggle: which entry shows the claude TUI, and who receives its output
  let termFor = $state<string | null>(null);
  let termSink: ((data: string) => void) | null = null;

  $effect(() => {
    for (const m of machines) if (!lives.has(m.url)) lives.set(m.url, new api.Live(m, (e) => onEvent(e), () => {}));
    for (const [url, l] of lives) if (!machines.some((m) => m.url === url)) { l.close(); lives.delete(url); }
  });

  function setEntry(id: string, patch: Partial<Entry>) {
    for (const t of Object.values(tree)) { const e = t.entries.find((x) => x.id === id); if (e) Object.assign(e, patch); }
    if (selected?.entry.id === id) Object.assign(selected.entry, patch);
  }
  function onEvent(e: api.LiveEvent) {
    if (e.type === 'state') {
      setEntry(e.entry, { state: e.state as Entry['state'] });
      if (e.state !== 'working' && e.state !== 'waiting') { streaming[e.entry] = ''; queued[e.entry] = 0; }
    } else if (e.type === 'delta') {
      streaming[e.entry] = (streaming[e.entry] ?? '') + e.text;
    } else if (e.type === 'message') {
      if (selected?.entry.id === e.entry) messages = [...messages, e.message];
      if (e.message.type === 'assistant') streaming[e.entry] = '';
    } else if (e.type === 'permission_request') {
      prompts[e.entry] = [...(prompts[e.entry] ?? []), { request_id: e.request_id, tool: e.tool, input: e.input }];
    } else if (e.type === 'permission_resolved') {
      prompts[e.entry] = (prompts[e.entry] ?? []).filter((p) => p.request_id !== e.request_id);
    } else if (e.type === 'mode') {
      setEntry(e.entry, { mode: e.mode });
    } else if (e.type === 'model') {
      setEntry(e.entry, { model: e.model });
    } else if (e.type === 'account') {
      setEntry(e.entry, { account: e.account });
    } else if (e.type === 'queued') {
      queued[e.entry] = e.count;
    } else if (e.type === 'commands') {
      if (selected) loadCommands(selected.machine);
    } else if (e.type === 'notice') {
      if (selected?.entry.id === e.entry) messages = [...messages, { type: 'assistant', uuid: crypto.randomUUID(), message: { content: [{ type: 'text', text: `— ${e.text} —` }] } }];
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
      live()!.send(selected.entry.id, draft, images);
      const content = [...images.map((im) => ({ type: 'image', source: { type: 'base64', ...im } })), { type: 'text', text: draft }];
      messages = [...messages, { type: 'user', uuid: crypto.randomUUID(), message: { role: 'user', content } }];
      draft = ''; attachments = []; liveErr = '';
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
    selected = { machine, entry };
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

  const stateLabel: Record<string, string> = {
    working: 'Working', waiting: 'Waiting for you', new: 'New: send a message to start it', stopped: 'Stopped: send a message to resume it', ready: 'Live and ready',
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
    dialog = 'add'; aError = ''; aPick = null;
    aMachine = aMachine ?? machines[0] ?? null;
    await search();
  }
  async function search() {
    if (!aMachine) return;
    try { const r = await api.sessions(aMachine, aQuery, 30); aSessions = r.sessions; aTotal = r.total; }
    catch (e) { aError = (e as Error).message; }
  }
  function pick(s: Session) {
    if (s.entry_id) return;
    aPick = s;
    aName = s.custom_title || s.summary || 'Untitled';
    aGroup = '';
  }
  async function add(ev: Event) {
    ev.preventDefault();
    if (!aMachine || !aPick) return;
    try {
      const e = await api.addEntry(aMachine, { session_id: aPick.session_id, name: aName, group: aGroup || undefined });
      dialog = null;
      await refresh();
      open(aMachine, e);
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

<svelte:window onkeydown={onKey} />

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
        <span class="chip" title={m.url}><span class="dot" class:on={!tree[m.url]?.error} class:off={!!tree[m.url]?.error}></span>{m.name}
          <span class="muted">{tree[m.url]?.error ? 'offline' : 'online'}</span></span>
      {/each}
      <button class="chip dashed" onclick={() => (dialog = 'connect')}>+ Connect a machine</button>
      <button class="chip icon" aria-label="Report a bug" title="Report a bug" onclick={() => { dialog = 'report'; rDone = ''; rReport = ''; }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="8" y="6" width="8" height="14" rx="4" /><path d="M19 7l-3 2M5 7l3 2M19 19l-3-2M5 19l3-2M20 13h-4M4 13h4M10 4l1 2M14 4l-1 2" /></svg>
      </button>
      <button class="chip icon" aria-label="Settings" title="Settings" onclick={openSettings}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
      </button>
    </div>
  </header>

  <div class="body">
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
              <div class="group">{g}</div>
              {#each es as e (e.id)}
                <button class="entry" class:sel={selected?.entry.id === e.id} class:waiting={e.state === 'waiting'} title={stateLabel[e.state]} onclick={() => open(m, e)}>
                  <span class="st {e.state}"></span>
                  {#if e.pinned}<span class="pin" aria-label="pinned">★</span>{/if}
                  <span class="ename">{e.name}</span>
                  <span class="muted small">{e.session ? ago(e.session.last_modified) : ''}</span>
                </button>
              {/each}
            {/each}
            {#if tree[m.url] && tree[m.url].entries.length === 0 && !tree[m.url].error}<p class="empty small">Nothing added yet.</p>{/if}
          </div>
        {/each}
      </div>
      <div class="navfoot"><button class="btn" disabled={!machines.length} onclick={openAdd}>+ Add</button></div>
    </nav>

    <main class="term">
      {#if selected}
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
        {#if termFor === selected.entry.id}
          {#key selected.entry.id}<TerminalView live={live()!} entry={selected.entry.id} listen={(fn) => (termSink = fn)} />{/key}
        {:else}
        <div class="log mono" role="log" aria-label="Session history" bind:this={logEl} onscroll={onLogScroll}>
          {#if loadingHistory}<div class="muted">Loading…</div>{/if}
          {#each messages as msg (msg.uuid)}
            {#each api.render(msg) as line}
              {#if line.kind === 'image'}
                <img class="shot" src={line.text} alt="Attachment" />
              {:else if line.kind === 'text'}
                {#if msg.type === 'user'}
                  <div class="you"><span class="prompt">❯ </span>{line.text}</div>
                {:else}
                  <div class="reply"><span class="bullet">⏺</span><div class="md">{@html md(line.text)}</div></div>
                {/if}
              {:else if line.kind === 'tool'}
                <div><span class="bullet">⏺ </span><span class="tool">{line.text}</span></div>
              {:else}
                <div class="result">⎿ {line.text}</div>
              {/if}
            {/each}
          {/each}
          {#if streaming[selected.entry.id]}<div class="streaming"><span class="bullet">⏺ </span>{streaming[selected.entry.id]}<span class="cursor">▌</span></div>{/if}
          {#each prompts[selected.entry.id] ?? [] as p (p.request_id)}
            <div class="perm" role="group" aria-label="Permission needed">
              <div class="permtitle">Permission needed</div>
              <div>{p.tool}: {summary(p.input)}</div>
              <div class="permactions">
                <button class="allow" onclick={() => act((l) => l.answer(p.request_id, true))}>Allow</button>
                <button class="deny" onclick={() => act((l) => l.answer(p.request_id, false))}>Deny</button>
              </div>
            </div>
          {/each}
        </div>
        {#if liveErr}<div class="liveerr" role="alert">{liveErr}</div>{/if}
        {#if queued[selected.entry.id]}<div class="queued muted small" aria-live="polite">{queued[selected.entry.id]} queued: sends when Claude finishes</div>{/if}
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
            <input bind:value={draft} onpaste={onPaste} onkeydown={onInputKey} disabled={selected.entry.state === 'terminal' || selected.entry.state === 'missing'}
              placeholder={selected.entry.state === 'terminal' ? 'Open in a terminal elsewhere' : selected.entry.state === 'working' ? 'Working… messages are queued (Esc to interrupt)' : 'Message this session'} /></label>
          <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden bind:this={fileInput} onchange={(e) => { addFiles((e.target as HTMLInputElement).files ?? []); (e.target as HTMLInputElement).value = ''; }} />
          <button type="button" class="tiny" aria-label="Attach images" title="Attach images (or paste / drop them)" onclick={() => fileInput?.click()}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" /></svg>
          </button>
          {#if selected.entry.state === 'working'}<button type="button" class="tiny" onclick={() => act((l, id) => l.interrupt(id))}>Esc · interrupt</button>{/if}
        </form>
        {/if}
      {:else}
        <div class="placeholder">Pick a session from the tree, or <button class="link" disabled={!machines.length} onclick={openAdd}>add one</button>.</div>
      {/if}
    </main>
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
      <label class="modepick">mode
        <select aria-label="Permission mode" value={selected.entry.mode ?? 'default'} onchange={(ev) => act((l, id) => l.setMode(id, (ev.target as HTMLSelectElement).value))}>
          {#each api.MODES as m}<option value={m}>{m}</option>{/each}
        </select>
      </label>
    {/if}
    <span class="grow"></span>
    <span>{counts.working ?? 0} working · {counts.waiting ?? 0} waiting · {counts.ready ?? 0} live · {counts.stopped ?? 0} stopped · {counts.terminal ?? 0} in terminal</span>
  </footer>
</div>

{#if dialog === 'connect'}
  <div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && (dialog = null)}>
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

{#if dialog === 'report'}
  <div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && (dialog = null)}>
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
  <div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && (dialog = null)}>
    <div class="dialog" role="dialog" aria-label="Settings">
      <h2>Settings</h2>
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
  <div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && (dialog = null)}>
    <form class="dialog wide" aria-label="Add to Switchboard" onsubmit={(e) => (aTab === 'new' ? createNew(e) : add(e))}>
      <h2>Add to Switchboard</h2>
      <div class="tabs" role="tablist" aria-label="Add from">
        <button type="button" role="tab" aria-selected={aTab === 'existing'} class:on={aTab === 'existing'} onclick={() => (aTab = 'existing')}>Existing session</button>
        <button type="button" role="tab" aria-selected={aTab === 'new'} class:on={aTab === 'new'} onclick={newTab}>New session</button>
      </div>
      <div class="pills">
        <span class="muted small">Machine</span>
        {#each machines as m (m.url)}
          <button type="button" class="pill" class:on={aMachine?.url === m.url} onclick={() => { aMachine = m; aPick = null; aTab === 'new' ? newTab() : search(); }}>{m.name}</button>
        {/each}
      </div>
      {#if aTab === 'new'}
        <label>Folder on {aMachine?.name}
          <input bind:value={nFolder} list="folder-list" required placeholder="/path/to/project" />
          <datalist id="folder-list">{#each nFolders as f}<option value={f}></option>{/each}</datalist>
        </label>
        <div class="grid2">
          <label>Name in the tree<input bind:value={nName} placeholder="New session" /></label>
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
      <div class="slist" role="radiogroup" aria-label="Sessions">
        {#each aSessions as s (s.session_id)}
          <button type="button" class="srow" class:on={aPick?.session_id === s.session_id} class:intree={!!s.entry_id} disabled={!!s.entry_id} onclick={() => pick(s)}>
            <span class="stitle">{s.custom_title || s.summary || 'Untitled'}{#if s.entry_id}<span class="badge">in tree</span>{/if}</span>
            <span class="mono muted small ellip">{s.cwd}{s.git_branch ? ` · ${s.git_branch}` : ''}</span>
            <span class="muted small">{ago(s.last_modified)}</span>
          </button>
        {/each}
        <p class="muted small">Showing {aSessions.length} of {aTotal}</p>
      </div>
      {#if aPick}
        <div class="grid2">
          <label>Name in the tree<input bind:value={aName} required /></label>
          <label>Group<input bind:value={aGroup} placeholder="default: the project folder" /></label>
        </div>
      {/if}
      {#if aError}<p class="error">{aError}</p>{/if}
      <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button><button class="btn primary" disabled={!aPick}>Add to tree</button></div>
      {/if}
    </form>
  </div>
{/if}

<style>
  /* Catppuccin Mocha */
  :global(:root) {
    --crust: #11111b; --mantle: #181825; --base: #1e1e2e; --s0: #313244; --s1: #45475a; --s2: #585b70;
    --o1: #7f849c; --o2: #9399b2; --sub0: #a6adc8; --sub1: #bac2de; --text: #cdd6f4;
    --mauve: #cba6f7; --green: #a6e3a1; --peach: #fab387; --sky: #89dceb; --red: #f38ba8;
  }
  :global(html, body) { margin: 0; height: 100%; background: var(--crust); color: var(--text); font: 13px/1.45 Inter, system-ui, sans-serif; }
  :global(button) { font: inherit; cursor: pointer; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
  .muted { color: var(--o2); } .small { font-size: 11px; } .grow { flex: 1; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  code { background: var(--s0); padding: 1px 5px; border-radius: 4px; font-size: 12px; }

  .app { display: flex; flex-direction: column; height: 100vh; background: var(--base); }
  .titlebar { display: flex; align-items: center; gap: 16px; padding: 10px 16px; background: var(--mantle); border-bottom: 1px solid var(--s0); flex-wrap: wrap; }
  .brand { display: flex; gap: 8px; align-items: center; font-weight: 600; font-size: 14px; }
  .chips { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: var(--s0); border: 1px solid var(--s1); color: var(--text); }
  .chip.icon { padding: 4px 8px; cursor: pointer; }
  .toggle { display: flex !important; flex-direction: row !important; gap: 10px; align-items: flex-start; color: var(--text) !important; font-size: 13px !important; }
  .toggle input { width: auto; margin-top: 3px; }
  .chip.dashed { background: none; border-style: dashed; border-color: var(--s2); color: var(--sub0); }
  .dot { width: 8px; height: 8px; border-radius: 50%; box-sizing: border-box; }
  .dot.on { background: var(--green); } .dot.off { border: 1.5px solid var(--o1); }

  .body { flex: 1; display: flex; min-height: 0; }
  .tree { width: 290px; flex: 0 0 290px; display: flex; flex-direction: column; background: var(--mantle); border-right: 1px solid var(--s0); }
  .search { display: block; padding: 12px; }
  input:focus, select:focus { outline: 1px solid var(--mauve); outline-offset: 0; }
  input { width: 100%; box-sizing: border-box; background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; }
  .scroll { flex: 1; overflow: auto; padding: 0 8px 12px; }
  .empty { color: var(--o2); padding: 8px; }
  .machine { margin-top: 6px; } .machine.offline { opacity: 0.6; }
  .mhead { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
  .mname { font-weight: 600; flex: 1; }
  .tiny.on { border-color: var(--mauve); color: var(--text); }
  .tiny { background: none; border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 2px 8px; font-size: 12px; }
  .group { padding: 6px 8px 2px 24px; color: var(--sub0); }
  .entry { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: 0; border-radius: 8px; padding: 6px 8px 6px 36px; background: none; color: var(--sub1); }
  .entry:hover { background: var(--base); }
  .entry.sel { background: var(--s0); color: var(--text); box-shadow: inset 2px 0 0 var(--mauve); }
  .entry.waiting { font-weight: 600; }
  .ename { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pin { color: var(--peach); font-size: 11px; }
  .st { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; box-sizing: border-box; border: 1.5px solid var(--o1); }
  .st.working { background: var(--green); border: 0; } .st.ready { border: 2px solid var(--green); } .st.waiting { border: 2px solid var(--peach); }
  .st.new { border: 1.5px dashed var(--mauve); }
  .st.terminal { background: var(--sky); border: 0; border-radius: 2px; } .st.missing, .st.error { border-color: var(--red); }
  .navfoot { padding: 10px 12px; border-top: 1px solid var(--s0); }
  .btn { width: 100%; background: var(--s0); border: 1px solid var(--s1); color: var(--text); border-radius: 8px; padding: 9px; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn.primary { background: var(--mauve); color: var(--crust); border: 0; font-weight: 600; width: auto; padding: 9px 18px; }
  .btn.ghost { width: auto; padding: 9px 16px; }

  .term { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--base); }
  .termhead { display: flex; gap: 16px; align-items: center; padding: 8px 18px; font-size: 12px; color: var(--o2); border-bottom: 1px solid var(--s0); }
  .log { flex: 1; overflow: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  .you { color: var(--text); }
  .reply { display: flex; gap: 8px; white-space: normal; }
  .md { min-width: 0; font-family: Inter, system-ui, sans-serif; line-height: 1.55; }
  .md :global(p) { margin: 0 0 8px; } .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-size: 14px; margin: 10px 0 6px; color: var(--mauve); }
  .md :global(code) { font-family: 'JetBrains Mono', monospace; background: var(--s0); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .md :global(pre) { background: var(--mantle); border: 1px solid var(--s0); border-radius: 8px; padding: 10px 12px; overflow-x: auto; }
  .md :global(pre code) { background: none; padding: 0; }
  .md :global(table) { border-collapse: collapse; margin: 6px 0; font-size: 12px; display: block; overflow-x: auto; }
  .md :global(th), .md :global(td) { border: 1px solid var(--s1); padding: 3px 8px; text-align: left; }
  .md :global(th) { background: var(--s0); }
  .md :global(a) { color: var(--sky); }
  .md :global(ul), .md :global(ol) { margin: 4px 0 8px; padding-left: 20px; } .prompt { color: var(--mauve); font-weight: 600; } .bullet { color: var(--mauve); }
  .tool { color: var(--sub1); } .result { color: var(--o2); padding-left: 18px; }
  .inputbar { display: flex; gap: 10px; align-items: center; padding: 12px 18px; border-top: 1px solid var(--s0); }
  .inputbar input { border: 0; background: none; }
  .placeholder { margin: auto; color: var(--o2); }
  .streaming { color: var(--text); } .cursor { color: var(--mauve); animation: blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .perm { border: 1px solid rgba(250, 179, 135, 0.45); background: rgba(250, 179, 135, 0.08); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; white-space: normal; }
  .permtitle { color: var(--peach); font-weight: 600; }
  .permactions { display: flex; gap: 8px; font-family: Inter, system-ui, sans-serif; }
  .allow { background: var(--green); color: var(--crust); border: 0; border-radius: 8px; padding: 8px 16px; font-weight: 600; }
  .deny { background: none; color: var(--red); border: 1px solid rgba(243, 139, 168, 0.45); border-radius: 8px; padding: 8px 16px; }
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

  .backdrop { position: fixed; inset: 0; background: rgba(17, 17, 27, 0.7); display: flex; align-items: center; justify-content: center; padding: 16px; }
  .dialog { width: 100%; max-width: 440px; background: var(--base); border: 1px solid var(--s1); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  .dialog.wide { max-width: 780px; max-height: 90vh; overflow: auto; }
  .dialog h2 { margin: 0; font-size: 16px; }
  .dialog label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--sub0); }
  .dialog .search { padding: 0; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .error { color: var(--red); margin: 0; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--s0); }
  .tabs button { background: none; border: 0; border-bottom: 2px solid transparent; color: var(--sub0); padding: 6px 12px; }
  .tabs button.on { border-bottom-color: var(--mauve); color: var(--text); font-weight: 600; }
  .dialog select { background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; appearance: none; }
  .sub { margin: 8px 0 0; font-size: 13px; }
  .updbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 16px; background: rgba(203, 166, 247, 0.12); border-bottom: 1px solid rgba(203, 166, 247, 0.35); }
  .updnotes { padding: 8px 16px 12px; background: var(--mantle); border-bottom: 1px solid var(--s0); max-height: 200px; overflow: auto; }
  .btn.small { padding: 5px 12px; }
  .dialog textarea { width: 100%; box-sizing: border-box; background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; resize: vertical; }
  .acclist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .acclist li { display: grid; grid-template-columns: 120px 1fr auto; gap: 10px; align-items: center; padding: 6px 10px; background: var(--mantle); border-radius: 8px; }
  .accform { display: flex; flex-direction: column; gap: 8px; }
  .dialog label.inline { flex-direction: row; align-items: center; gap: 8px; } .dialog label.inline input { width: auto; }
  .dialog { max-height: 90vh; overflow: auto; }
  .pills { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .pill { background: var(--mantle); border: 1px solid var(--s1); color: var(--sub1); border-radius: 999px; padding: 6px 12px; }
  .pill.on { background: var(--s0); border-color: var(--mauve); color: var(--text); }
  .slist { display: flex; flex-direction: column; gap: 6px; }
  .srow { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr) 50px; gap: 12px; align-items: center; text-align: left; padding: 10px 12px; border-radius: 10px; background: var(--mantle); border: 1px solid var(--s0); color: var(--text); }
  .srow.on { background: var(--s0); border-color: var(--mauve); }
  .srow.intree { opacity: 0.6; cursor: default; }
  .stitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { margin-left: 8px; font-size: 11px; color: var(--sub0); border: 1px solid var(--s1); border-radius: 999px; padding: 1px 8px; }
  .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
</style>
