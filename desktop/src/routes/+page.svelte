<script lang="ts">
  import * as api from '$lib/api';
  import { pairLocal, serviceStatus, serviceInstall, serviceUninstall, inShell } from '$lib/shell';
  import type { ServiceStatus } from '$lib/shell';
  import TerminalView from '$lib/Terminal.svelte';
  import type { Machine, Entry, Session, HistoryMessage } from '$lib/api';

  // the HTML autofocus attribute only works on page load, not on elements Svelte adds later
  const focusOnMount = (node: HTMLElement) => { node.focus(); };

  // ---- state ----
  let machines = $state<Machine[]>(api.loadMachines());
  let tree = $state<Record<string, { entries: Entry[]; error?: string }>>({});
  let selected = $state<{ machine: Machine; entry: Entry } | null>(null);
  let messages = $state<HistoryMessage[]>([]);
  let loadingHistory = $state(false);
  let filter = $state('');
  let dialog = $state<'connect' | 'add' | 'settings' | null>(null);

  // settings: background service
  let svc = $state<ServiceStatus | null>(null);
  let svcBusy = $state(false);
  let svcError = $state('');
  async function openSettings() { dialog = 'settings'; svcError = ''; svc = await serviceStatus(); }
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

  // ---- live sessions (phase 2) ----
  const lives = new Map<string, api.Live>();
  let streaming = $state<Record<string, string>>({});
  let prompts = $state<Record<string, { request_id: string; tool: string; input: any }[]>>({});
  let draft = $state('');
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
      if (e.state !== 'working' && e.state !== 'waiting') streaming[e.entry] = '';
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
    if (!selected || !draft.trim()) return;
    try {
      live()!.send(selected.entry.id, draft);
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
    selected = { machine, entry };
    loadingHistory = true;
    try { messages = await api.history(machine, entry.id); }
    catch (e) { messages = []; }
    loadingHistory = false;
  }

  const stateLabel: Record<string, string> = {
    working: 'Working', waiting: 'Waiting for you', stopped: 'Stopped: send a message to resume it', ready: 'Live and ready',
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
        <div class="log mono" role="log" aria-label="Session history">
          {#if loadingHistory}<div class="muted">Loading…</div>{/if}
          {#each messages as msg (msg.uuid)}
            {#each api.render(msg) as line}
              {#if line.kind === 'text'}
                <div class:you={msg.type === 'user'}>{#if msg.type === 'user'}<span class="prompt">❯ </span>{:else}<span class="bullet">⏺ </span>{/if}{line.text}</div>
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
        <form class="inputbar mono" onsubmit={sendDraft}>
          <span class="prompt">❯</span>
          <label class="grow"><span class="sr">Message this session</span>
            <input bind:value={draft} disabled={selected.entry.state === 'terminal' || selected.entry.state === 'missing'}
              placeholder={selected.entry.state === 'terminal' ? 'Open in a terminal elsewhere' : selected.entry.state === 'working' ? 'Working… (Esc to interrupt)' : 'Message this session'} /></label>
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
      <label class="modepick">mode
        <select value={selected.entry.mode ?? 'default'} onchange={(ev) => act((l, id) => l.setMode(id, (ev.target as HTMLSelectElement).value))}>
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
      <div class="actions"><button class="btn ghost" onclick={() => (dialog = null)}>Close</button></div>
    </div>
  </div>
{/if}

{#if dialog === 'add'}
  <div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && (dialog = null)}>
    <form class="dialog wide" aria-label="Add to Switchboard" onsubmit={add}>
      <h2>Add to Switchboard</h2>
      <div class="pills">
        <span class="muted small">Machine</span>
        {#each machines as m (m.url)}
          <button type="button" class="pill" class:on={aMachine?.url === m.url} onclick={() => { aMachine = m; aPick = null; search(); }}>{m.name}</button>
        {/each}
      </div>
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
  .st.terminal { background: var(--sky); border: 0; border-radius: 2px; } .st.missing, .st.error { border-color: var(--red); }
  .navfoot { padding: 10px 12px; border-top: 1px solid var(--s0); }
  .btn { width: 100%; background: var(--s0); border: 1px solid var(--s1); color: var(--text); border-radius: 8px; padding: 9px; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn.primary { background: var(--mauve); color: var(--crust); border: 0; font-weight: 600; width: auto; padding: 9px 18px; }
  .btn.ghost { width: auto; padding: 9px 16px; }

  .term { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--base); }
  .termhead { display: flex; gap: 16px; align-items: center; padding: 8px 18px; font-size: 12px; color: var(--o2); border-bottom: 1px solid var(--s0); }
  .log { flex: 1; overflow: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  .you { color: var(--text); } .prompt { color: var(--mauve); font-weight: 600; } .bullet { color: var(--mauve); }
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
