<script lang="ts">
  import * as api from '$lib/api';
  import { pairLocal, serviceStatus, serviceInstall, serviceUninstall, inShell, openExternal, openLocal, appVersion, checkForUpdate, notify,
    soloOf, popOut, takeHandoff, focusWindow, onSoloClosing, onSoloEvents, soloOpened, closeThisWindow, setOnTop, useParentShell, canOpenLocally,
    isPhoneApp, isDesktopApp, pairLink, scanPairCode, setPhonePush, takeNotificationOpen } from '$lib/shell';
  import qrcode from 'qrcode-generator';
  import { backdrop } from '$lib/backdrop';
  import { theme, THEMES, applyTheme, fonts, UI_FONTS, MONO_FONTS, CHAT_FONTS, SIZES, applyFonts } from '$lib/themes.svelte';
  applyTheme(theme.name);
  applyFonts();

  // links (in Claude's replies, the inbox, questions) never navigate this window: web and mail links open in the
  // default browser / mail app, anything else is ignored. ponytail: until sessions get windows or tabs of their own
  // ---- background tasks: shells and other non-agent tasks (background agents already show in the tree) ----
  // the slim bar shows only running ones; it (and ⋯ → Background tasks) opens the list of running and finished
  const bgTasks = (e: Entry) => (e.tasks ?? []).filter((t) => !/agent/i.test(t.type ?? ''));

  const bgRunning = (e: Entry) => bgTasks(e).filter((t) => t.status === 'running').length;
  let bgOpen = $state(false);
  let clock = $state(Date.now());
  $effect(() => { const t = setInterval(() => (clock = Date.now()), 15_000); return () => clearInterval(t); });
  const span = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`; };
  // copy buttons: a reply (its markdown) or a code block in one (button.copy inside the block, from markdown.ts)
  async function copyText(text: string, btn?: HTMLElement | null) {
    try { await api.copy(text); } catch { return say("Couldn't copy"); }
    if (!btn) return;
    btn.classList.add('copied');
    if (btn.classList.contains('copy')) btn.textContent = 'Copied';
    setTimeout(() => { btn.classList.remove('copied'); if (btn.classList.contains('copy')) btn.textContent = 'Copy'; }, 1500);
  }
  function onCopyClick(ev: MouseEvent) {
    const b = (ev.target as Element | null)?.closest?.('pre > button.copy') as HTMLElement | null;
    if (b) copyText(b.parentElement?.querySelector('code')?.textContent ?? '', b);
  }
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
    if (canOpenLocally() && isLocal(selected.machine)) {
      try { if ((await openLocal(abs)) === 'revealed') say(`Shown in its folder: ${abs}`); }
      catch (e) { say(String(e)); }
    } else {
      try { await api.copy(abs); say(`Copied: ${abs}${inShell() ? ` (it's on ${selected.machine.name})` : ''}`); }
      catch { say(abs); }
    }
  }
  $effect(() => {
    document.addEventListener('click', onPathClick);
    document.addEventListener('click', onCopyClick);
    document.addEventListener('click', onLinkClick, true);
    document.addEventListener('auxclick', onLinkClick, true);  // middle-click too
    return () => { document.removeEventListener('click', onCopyClick); document.removeEventListener('click', onPathClick); document.removeEventListener('click', onLinkClick, true); document.removeEventListener('auxclick', onLinkClick, true); };
  });
  import type { Update } from '$lib/shell';
  import { captureAppLog, buildReport, issueUrl } from '$lib/report';
  import type { ServiceStatus } from '$lib/shell';
  import TerminalView from '$lib/Terminal.svelte';
  import pkg from '../../package.json';
  import InboxView from '$lib/Inbox.svelte';
  import WatchesView from '$lib/Watches.svelte';
  import PromptCard from '$lib/Prompt.svelte';
  import { md } from '$lib/markdown';
  import { tick, untrack } from 'svelte';
  import type { Machine, Entry, Session, HistoryMessage } from '$lib/api';

  // the HTML autofocus attribute only works on page load, not on elements Svelte adds later
  const focusOnMount = (node: HTMLElement) => { node.focus(); };

  // ---- state ----
  // a popped-out window shows one session (shell.ts popOut); the main window owns everything else
  const solo = soloOf();
  let machines = $state<Machine[]>(api.loadMachines());
  let tree = $state<Record<string, { entries: Entry[]; error?: string }>>({});
  let selected = $state<{ machine: Machine; entry: Entry; sub?: api.Sub } | null>(null);
  let sessOpts = $state(false);  // the phone's sheet with the model / effort / mode / account pickers (the status bar has them on a desktop)
  // Background tasks list (from the slim bar or ⋯): which session it shows, kept current from the tree
  let tasksFor = $state<{ machine: Machine; entry: Entry } | null>(null);
  const tasksEntry = $derived(tasksFor ? tree[tasksFor.machine.url]?.entries.find((x) => x.id === tasksFor!.entry.id) ?? tasksFor.entry : null);
  let messages = $state<HistoryMessage[]>([]);
  let loadingHistory = $state(false);
  let openSeq = 0;
  let filter = $state('');
  let dialog = $state<'connect' | 'add' | 'settings' | 'report' | 'watches' | 'delete' | 'removed' | null>(null);

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
    if (solo) return;
    const first = setTimeout(() => checkUpdates(), 10_000);
    const every = setInterval(() => checkUpdates(), 6 * 3600_000);
    return () => { clearTimeout(first); clearInterval(every); };
  });
  let liveHere = $derived(Object.values(tree).flatMap((t) => t.entries).filter((e) => ['working', 'waiting', 'ready'].includes(e.state)).length);
  async function installUpdate() {
    if (!update) return;
    updBusy = 0;
    try { await update.install((p) => (updBusy = p)); if (isPhoneApp()) updBusy = null; }  // the phone: the browser downloads it
    catch (e) { updBusy = null; updMsg = `Update failed: ${(e as Error).message ?? e}`; }
  }

  // report a bug
  let rDesc = $state('');
  let rLogs = $state(true);
  let rBusy = $state(false);
  let rDone = $state('');
  let rReport = $state('');
  // in the app: file the issue directly (a one-time GitHub device sign-in); or open GitHub's page as before
  let gh = $state<api.GitHub | null>(null);
  let ghCode = $state<{ user_code: string; verification_uri: string } | null>(null);
  let rIssue = $state('');
  let ghSeq = 0;
  async function loadGh() { gh = localMachine ? await api.githubStatus(localMachine).catch(() => null) : null; }
  async function signInGitHub(m: Machine) {
    const d = await api.githubStart(m);
    ghCode = d;
    try { await api.copy(d.user_code); } catch {}
    openExternal(d.verification_uri);
    const mine = ++ghSeq, until = Date.now() + d.expires_in * 1000;
    let wait = d.interval;
    try {
      while (mine === ghSeq && Date.now() < until) {
        await new Promise((ok) => setTimeout(ok, wait * 1000));
        if (mine !== ghSeq) return;
        const r = await api.githubPoll(m, d.device_code);
        if (r.status === 'done') { gh = { signed_in: true, login: r.login ?? null }; return; }
        if (r.status === 'slow_down') wait += 5;
        else if (r.status !== 'pending') throw new Error(r.status === 'denied' ? 'GitHub sign-in was cancelled' : 'The sign-in code expired: try again');
      }
    } finally { if (mine === ghSeq) ghCode = null; }
  }
  async function sendToGitHub() {
    const m = localMachine;
    if (!m || !rDesc.trim()) return;
    rBusy = true; rDone = ''; rIssue = '';
    try {
      if (!gh?.signed_in) await signInGitHub(m);
      if (!gh?.signed_in) return;
      rReport = await buildReport(rDesc, machines, rLogs, await appVersion());
      const r = await api.githubIssue(m, rDesc.trim().split('\n')[0].slice(0, 90) || 'Bug report', rReport);
      rIssue = r.url; rDone = `Sent as issue #${r.number}. Thank you!`;
    } catch (e) {
      const c = (e as Error).message;
      if (c === 'not_signed_in') { gh = { signed_in: false, login: null }; rDone = 'GitHub sign-in has expired: press Send again to sign in.'; }
      else rDone = `Couldn't send it: ${c}. You can use "Open on GitHub instead".`;
    } finally { rBusy = false; }
  }
  async function sendReport(ev: Event) {
    ev.preventDefault();
    rBusy = true; rDone = ''; rIssue = '';
    rReport = await buildReport(rDesc, machines, rLogs, await appVersion());
    let copied = true;
    try { await api.copy(rReport); } catch { copied = false; }
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
  // ---- phone notifications ----
  let pushServer = $state('');
  let pushOn = $state(false);
  const PUSH_PREFS = 'switchboard.push.prefs', PUSH_TOPIC = 'switchboard.push.topic';
  let pushPrefs = $state<{ finished: boolean }>((() => {
    try { return { finished: JSON.parse(localStorage.getItem(PUSH_PREFS) ?? '{}').finished ?? true }; } catch { return { finished: true }; }
  })());
  const pushTopic = () => {
    let t = ''; try { t = localStorage.getItem(PUSH_TOPIC) ?? ''; } catch {}
    if (!t) { t = 'sb-' + api.uid().replace(/-/g, ''); try { localStorage.setItem(PUSH_TOPIC, t); } catch {} }  // unguessable: it's the only lock
    return t;
  };
  // the server from whichever machine has one; in the phone app, subscribe this phone on every machine and hand the
  // listener its config. Runs at start, after pairing, and when a setting changes.
  let pushCfg: { server: string; topic: string; machines: Machine[] } | null = null;  // what the listener has, bar the switches
  async function syncPush() {
    const infos = await Promise.all(machines.map((m) => api.push(m).catch(() => null)));
    pushServer = infos.find((i) => i?.server)?.server ?? pushServer;
    if (!isPhoneApp() || !pushServer) return;
    const topic = pushTopic(), url = `${pushServer.replace(/\/$/, '')}/${topic}`;
    const ok = await Promise.all(machines.map((m, i) => infos[i] ? api.setPush(m, { subscribe: url, machine_url: m.url }).then(() => m, () => null) : null));
    pushCfg = { server: pushServer, topic, machines: ok.filter((m): m is Machine => !!m).map((m) => $state.snapshot(m)) };
    await setPhonePush({ ...pushCfg, ...pushPrefs })
      .then(() => (pushOn = true), (e) => say(`Notifications: ${(e as Error).message ?? e}`));
  }
  async function savePushServer() {
    const s = pushServer.trim().replace(/\/$/, '');
    if (s && !/^https?:\/\//.test(s)) return say('The notification server address starts with http:// or https://');
    await Promise.all(machines.map((m) => api.setPush(m, { server: s }).catch(() => null)));
    await syncPush();
  }
  function savePushPrefs() {
    try { localStorage.setItem(PUSH_PREFS, JSON.stringify(pushPrefs)); } catch {}
    // straight to the listener: a full sync first asks every machine, and a phone locked meanwhile pauses the app before it saves
    if (pushCfg) setPhonePush({ ...pushCfg, ...pushPrefs }).catch(() => {});
    else syncPush();
  }
  // a tapped notification: open its session (once the tree has it: right after start it may still be loading)
  let fromNotification = $state<{ machine: string; entry: string } | null>(null);
  function openNotified() {
    const o = fromNotification, m = o && machines.find((x) => x.url.replace(/\/$/, '') === o.machine.replace(/\/$/, ''));
    const en = m && tree[m.url]?.entries.find((x) => x.id === o!.entry);
    if (m && en) { fromNotification = null; open(m, en, 'keep'); }
  }
  async function takeNotified() { fromNotification = (await takeNotificationOpen().catch(() => null)) ?? fromNotification; openNotified(); }
  setTimeout(syncPush, 1500);
  if (isPhoneApp()) {
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') takeNotified(); });
    takeNotified();
    $effect(() => { if (fromNotification && Object.keys(tree).length) openNotified(); });
  }
  const qrSvg = (text: string) => { const q = qrcode(0, 'M'); q.addData(text); q.make(); return q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); };
  async function newCode() {
    if (!localMachine) return;
    try { const c = await api.pairCode(localMachine); code = { code: c.code, until: Date.now() + c.expires_in * 1000 }; }
    catch (e) { netError = (e as Error).message; }
  }
  $effect(() => { if (!code) return; const t = setInterval(() => (now = Date.now()), 1000); return () => clearInterval(t); });
  // daemon-wide settings, per machine (null: that daemon is too old to have them)
  let dset = $state<Record<string, api.DaemonSettings | null>>({});
  const IDLE = [[0, 'Never'], [10, 'After 10 minutes'], [30, 'After 30 minutes'], [60, 'After 1 hour'], [240, 'After 4 hours']] as const;
  function loadDaemonSettings() {
    for (const m of machines) { api.settings(m).then((v) => (dset[m.url] = v), () => (dset[m.url] = null)); loadModels(m); loadAccounts(m); }
  }
  async function setDefault(m: Machine, key: keyof api.NewSessionDefaults, value: string) {
    const next = { ...(dset[m.url]?.new_session ?? {}), [key]: value || undefined };
    try { dset[m.url] = await api.setSettings(m, { new_session: next }); } catch (e) { say(`Couldn't change ${m.name}: ${(e as Error).message}`); }
  }
  async function setIdle(m: Machine, minutes: number) {
    try { dset[m.url] = await api.setSettings(m, { idle_close_minutes: minutes }); } catch (e) { say(`Couldn't change ${m.name}: ${(e as Error).message}`); }
  }
  async function openSettings() {
    loadNet(); loadDaemonSettings(); loadGh(); code = null;
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
  // opened from a daemon's own address (a phone over Tailscale): that's the machine to connect to
  let cUrl = $state(typeof location !== 'undefined' && !inShell() && location.port === '7878' ? location.origin : isPhoneApp() ? '' : 'http://127.0.0.1:7878');
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
  // the New session form starts from the machine's defaults (Settings → New sessions), so you see what you'll get
  $effect(() => {
    const d = aMachine ? dset[aMachine.url]?.new_session ?? {} : {};
    untrack(() => { nMode = d.mode ?? 'default'; nModel = d.model ?? 'default'; nAccount = d.account ?? ''; });
  });
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
  // edits' hunks (line numbers) by tool_use id, from history and live tool results
  let hunks = $derived(Object.assign({}, ...messages.map((m) => m.patches ?? {})) as Record<string, api.Hunk[]>);
  // Bash commands by tool_use id: a terminal window's title says "Bash" and its body starts with "$ <command>"
  let bashCmd = $derived(Object.fromEntries(messages.flatMap((m) => Array.isArray(m.message?.content) ? m.message.content : [])
    .filter((b: any) => b?.type === 'tool_use' && b.name === 'Bash' && b.input?.command).map((b: any) => [b.id, String(b.input.command)])) as Record<string, string>);
  // claude.ai Artifacts by tool_use id: the result shows as a card linking the published page
  let artifacts = $derived(Object.fromEntries(messages.flatMap((m) => Array.isArray(m.message?.content) ? m.message.content : [])
    .filter((b: any) => b?.type === 'tool_use' && b.name === 'Artifact' && (b.input?.action ?? 'publish') === 'publish')
    .map((b: any) => [b.id, String(b.input?.title || b.input?.file_path?.split('/').pop() || 'Artifact')])) as Record<string, string>);
  const artifactUpdated = (t: string) => Number(t.match(/\(Version (\d+)/)?.[1] ?? 1) > 1;  // "Published … at <url> (Version 2, …)"
  const artifactUrl = (t: string) => t.match(/https:\/\/claude\.ai\/(?:code\/)?artifact\/[\w-]+/)?.[0];
  const diffLines = (l: { lines: api.DiffLine[]; id?: string }) => (l.id && hunks[l.id] ? api.hunkLines(hunks[l.id]) : l.lines);
  const FILE_TOOLS = ['Read', 'NotebookEdit', 'NotebookRead'];  // Edit/Write are diffs, whose header carries the path too
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
    const btw = { name: 'btw', description: "Ask a side question: the answer opens on the right and doesn't go into the session" };
    return [btw, ...(cmdList[selected.machine.url] ?? []).filter((c) => c.name !== 'btw')].filter((c) => c.name.toLowerCase().includes(q))
      .sort((a, b) => Number(!a.name.toLowerCase().startsWith(q)) - Number(!b.name.toLowerCase().startsWith(q))).slice(0, 8);
  });
  function complete(c: api.Command) { draft = `/${c.name} `; cmdIdx = 0; }
  // multi-line box: Enter sends, Shift+Enter starts a new line, Ctrl+Enter sends now (interrupts), as in Claude Code
  let sendNow = false;
  function enterSends(ev: KeyboardEvent) {
    if (ev.key !== 'Enter' || ev.shiftKey || ev.isComposing || ev.defaultPrevented || (phone && !ev.ctrlKey && !ev.metaKey)) return;
    ev.preventDefault();
    sendNow = ev.ctrlKey || ev.metaKey;
    (ev.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
  }
  function grow(el: HTMLTextAreaElement) {  // fit the text, up to the CSS max-height
    const fit = () => { el.style.height = 'auto'; el.style.height = `${el.scrollHeight}px`; };
    $effect(() => { void draft; fit(); });  // also shrinks back after sending
    // and when its width changes: squeezed narrow it grew tall, and stayed tall once it had room again
    let w = 0;
    const ro = new ResizeObserver(() => { if (el.clientWidth !== w) { w = el.clientWidth; fit(); } });
    ro.observe(el);
    $effect(() => () => ro.disconnect());
  }
  // @-mentions: "@" then letters lists files in the session's folder; picking one inserts @path (Claude Code reads it)
  let atQ = $state<string | null>(null);
  let atList = $state<string[]>([]);
  let atIdx = $state(0);
  let inputEl = $state<HTMLTextAreaElement>();
  let atSeq = 0;
  function onDraftInput() {
    const el = inputEl;
    if (!el || !selected || selected.sub) return void (atQ = null);
    const m = el.value.slice(0, el.selectionStart).match(/(?:^|\s)@([^\s@"]*)$/);
    atQ = m ? m[1] : null;
    if (atQ === null) { atList = []; return; }
    const s = selected, q = atQ, mine = ++atSeq;
    setTimeout(async () => {
      if (mine !== atSeq) return;  // typed on: only the latest asks
      try { const r = await api.files(s.machine, s.entry.id, q); if (mine === atSeq) { atList = r.files.slice(0, 8); atIdx = 0; } }
      catch { if (mine === atSeq) atList = []; }
    }, 120);
  }
  function pickAt(f: string) {
    const el = inputEl;
    if (!el) return;
    const pos = el.selectionStart;
    const before = draft.slice(0, pos).replace(/@([^\s@"]*)$/, '@' + (/\s/.test(f) ? `"${f}"` : f) + ' ');
    draft = before + draft.slice(pos);
    atQ = null; atList = []; atSeq++;
    tick().then(() => { el.focus(); el.selectionStart = el.selectionEnd = before.length; });
  }
  function onInputKey(ev: KeyboardEvent) {
    if (atQ !== null && atList.length) {
      if (ev.key === 'ArrowDown') { ev.preventDefault(); atIdx = (atIdx + 1) % atList.length; return; }
      if (ev.key === 'ArrowUp') { ev.preventDefault(); atIdx = (atIdx - 1 + atList.length) % atList.length; return; }
      if (ev.key === 'Tab' || ev.key === 'Enter') { ev.preventDefault(); pickAt(atList[atIdx]); return; }
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); atQ = null; atList = []; atSeq++; return; }
    }
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
    if ('entry' in e && e.entry && (e.type === 'message' || e.type === 'delta' || (e.type === 'state' && e.state === 'working'))) seen[e.entry] = Date.now();
    if (e.type === 'inbox' && m) {
      inboxBy[m.url] = [e.item, ...(inboxBy[m.url] ?? [])];
      if (!solo) notify(`${e.item.source}${e.item.from_agent ? ' (agent)' : ''}`, `${e.item.title}: ${e.item.body}`);
      return;
    }
    if ((e.type === 'inbox_read' || e.type === 'inbox_removed') && m) { loadInbox(m); return; }
    if (e.type === 'settings' && m) { api.settings(m).then((v) => (dset[m.url] = v), () => {}); return; }
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
      if (e.message.type === 'assistant') { streaming[e.entry] = ''; thinkingLive[e.entry] = ''; if ((selected?.entry.id !== e.entry || selected.sub) && !inWindow[e.entry]) unread[e.entry] = true; }
    } else if (e.type === 'permission_request') {
      prompts[e.entry] = [...(prompts[e.entry] ?? []), { request_id: e.request_id, tool: e.tool, kind: e.kind ?? 'tool', input: e.input, remember: e.remember ?? [] }];
      // a session you're not looking at needs you: say so outside the app too
      // the main window speaks for sessions in its tabs, a popped-out window for its own (when it isn't in front)
      if (typeof document !== 'undefined' && (solo ? e.entry === solo.entry && !document.hasFocus() : !inWindow[e.entry] && (!document.hasFocus() || selected?.entry.id !== e.entry))) {
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
    } else if (e.type === 'tasks') {
      setEntry(e.entry, { tasks: e.tasks });
    } else if (e.type === 'session_reset') {  // /clear: the entry now holds a fresh conversation
      if (selected?.entry.id === e.entry && !selected.sub) messages = [{ type: 'assistant', uuid: api.uid(), message: { content: [{ type: 'text', text: '— Conversation cleared —' }] } }];
      refresh();
    } else if (e.type === 'notice') {
      if (selected?.entry.id === e.entry && !selected.sub) messages = [...messages, { type: 'assistant', uuid: api.uid(), message: { content: [{ type: 'text', text: `— ${e.text} —` }] } }];
    } else if (e.type === 'terminal_output') {
      if (e.entry === termFor) termSink?.(e.data);
    } else if (e.type === 'terminal_closed') {
      if (e.entry === termFor) { termFor = null; if (selected) open(selected.machine, selected.entry); }  // back to chat, fresh history
    } else if (e.type === 'btw') {
      const c = (btwChats[e.entry] ??= { items: [], streaming: '', busy: false });
      if (e.kind === 'asked') { c.items.push({ who: 'you', text: e.text ?? '' }); c.busy = true; c.streaming = ''; }
      else if (e.kind === 'delta') c.streaming += e.text ?? '';
      else if (e.kind === 'answer') {
        c.items.push({ who: 'claude', text: e.text ?? '' }); c.streaming = '';
        if (selected?.entry.id !== e.entry || selected.sub) unread[e.entry] = true;
      } else if (e.kind === 'done') { c.busy = false; c.streaming = ''; if (e.error) c.items.push({ who: 'claude', text: "_Couldn't answer that._" }); }
      else if (e.kind === 'closed') delete btwChats[e.entry];
    } else if (e.type === 'error') {
      if (e.message === 'nothing_to_ask_about' && e.entry && btwChats[e.entry]) { btwChats[e.entry].busy = false; btwChats[e.entry].items.push({ who: 'claude', text: '_Say something in this session first: a side question is about what has been said._' }); }
      else if (!e.entry || e.entry === selected?.entry.id) liveErr = e.message;
    }
  }
  const live = () => (selected ? lives.get(selected.machine.url) : undefined);
  // ---- /btw: a side question in a panel on the right; the session's history never sees it (daemon: a fork) ----
  let btwChats = $state<Record<string, { items: { who: 'you' | 'claude'; text: string }[]; streaming: string; busy: boolean }>>({});
  let btwDraft = $state('');
  let btwAside = $state<Record<string, boolean>>({});  // phone: the side chat stepped aside (‹), back via the header
  function askBtw(text: string) {
    const s = selected;
    if (!s) return;
    btwChats[s.entry.id] ??= { items: [], streaming: '', busy: false };
    btwAside[s.entry.id] = false;  // a new side question brings it to the front (on a phone)
    if (!text.trim()) return;  // "/btw" alone just opens the panel
    try { live()!.btw(s.entry.id, text.trim()); } catch (e) { liveErr = (e as Error).message; }
  }
  function closeBtw(entry: string) {
    try { live()?.btwClose(entry); } catch {}
    delete btwChats[entry];
  }
  function sendDraft(ev: Event) {
    ev.preventDefault();
    if (!selected || (!draft.trim() && !attachments.length)) return;
    if (/^\/btw(\s|$)/.test(draft) && !selected.sub) { askBtw(draft.slice(4)); draft = ''; return; }
    try {
      const images = attachments.map(({ media_type, data }) => ({ media_type, data }));
      live()!.send(selected.entry.id, draft, images, sendNow);  // now: the daemon interrupts and sends this first
      keepTab();
      const content = [...images.map((im) => ({ type: 'image', source: { type: 'base64', ...im } })), { type: 'text', text: draft }];
      const msg: HistoryMessage = { type: 'user', uuid: api.uid(), message: { role: 'user', content } };
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
      messages = [...messages, { type: 'user', uuid: api.uid(), message: { role: 'user', content: draft } }];
      draft = ''; liveErr = ''; keepTab();
    } catch (e) { liveErr = (e as Error).message; }
  }
  function act(fn: (l: api.Live, id: string) => void) {
    if (!selected) return;
    try { fn(live()!, selected.entry.id); liveErr = ''; } catch (e) { liveErr = (e as Error).message; }
  }
  function onKey(ev: KeyboardEvent) {
    if (ev.ctrlKey && !dialog && (ev.key === 'Tab' || ev.key === 'PageDown' || ev.key === 'PageUp')) {
      ev.preventDefault(); return cycleTab(ev.key === 'PageUp' || (ev.key === 'Tab' && ev.shiftKey) ? -1 : 1);
    }
    if (ev.ctrlKey && !dialog && ev.key.toLowerCase() === 'w' && (curKey || solo)) { ev.preventDefault(); return solo ? closeThisWindow() : closeTab(curKey!); }
    if (ev.key === 'Escape' && selected?.entry.state === 'working' && !dialog) act((l, id) => l.interrupt(id));
  }
  const summary = (input: any) => input?.command ?? input?.file_path ?? input?.pattern ?? input?.url ?? JSON.stringify(input).slice(0, 200);

  // ---- tree ----
  // ---- which Switchboard each machine runs: a different one than this app gets a tag on its chip ----
  let dver = $state<Record<string, string>>({});
  const verAsked: Record<string, number> = {};
  function checkVersion(m: Machine) {
    if (Date.now() - (verAsked[m.url] ?? 0) < 60_000) return;  // a daemon can update itself: ask again now and then
    verAsked[m.url] = Date.now();
    api.machineInfo(m).then((i) => (dver[m.url] = i.daemon), () => delete verAsked[m.url]);
  }
  // -1: older than this app, 1: newer, 0: same (or unknown)
  function verCmp(a: string, b: string) {
    const pa = a.split('.').map(Number), pb = b.split('.').map(Number);
    if ([...pa, ...pb].some(isNaN)) return 0;
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) < (pb[i] ?? 0) ? -1 : 1;
    return 0;
  }
  const verNote = (m: Machine) => {
    const d = dver[m.url], c = d ? verCmp(d, pkg.version) : 0;
    return !c ? null : c < 0
      ? { tag: `older: ${d}`, why: `${m.name} runs Switchboard ${d}; this app is ${pkg.version}. Some things may not work there until it's updated: open Switchboard on ${m.name} and restart to update.` }
      : { tag: `newer: ${d}`, why: `${m.name} runs Switchboard ${d}, newer than this app (${pkg.version}). Update this app: Settings → Check for updates.` };
  };

  async function refresh() {
    const next: typeof tree = {};
    await Promise.all(machines.map(async (m) => {
      try { next[m.url] = { entries: await api.entries(m) }; checkVersion(m); if (!(m.url in dset)) api.settings(m).then((v) => (dset[m.url] = v), () => (dset[m.url] = null)); }
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
    (solo ? Promise.resolve() : ensureLocal()).then(refresh);
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  });

  // within a project: pinned first, then the most recently active (the session file's last change, or a live event
  // seen just now); the order holds still while the pointer is over the tree, so nothing jumps under a click
  let seen = $state<Record<string, number>>({});
  const activity = (e: Entry) => Math.max(e.session?.last_modified ?? 0, seen[e.id] ?? 0);
  let held = $state<Record<string, number> | null>(null);  // entry id -> position, while held
  function holdOrder() {
    const pos: Record<string, number> = {};
    for (const m of machines) for (const [, es] of groups(m)) es.forEach((e, i) => (pos[e.id] = i));
    held = pos;
  }
  function groups(m: Machine) {
    const es = (tree[m.url]?.entries ?? []).filter((e) => !filter || (e.name + ' ' + e.group).toLowerCase().includes(filter.toLowerCase()));
    const g: Record<string, Entry[]> = {};
    for (const e of es) (g[e.group] ??= []).push(e);
    const h = held;
    for (const list of Object.values(g))
      list.sort(h ? (a, b) => (h[a.id] ?? 1e9) - (h[b.id] ?? 1e9) : (a, b) => Number(b.pinned) - Number(a.pinned) || activity(b) - activity(a));
    // projects: in the order dragged into (kept by that machine's daemon); ones not placed yet follow, as before
    const order = dset[m.url]?.project_order ?? [];
    const at = (name: string, i: number) => { const k = order.indexOf(name); return k < 0 ? 1e6 + i : k; };
    return Object.entries(g).map((x, i) => [x, at(x[0], i)] as const).sort((a, b) => a[1] - b[1]).map(([x]) => x);
  }
  let dragGroup: { url: string; g: string } | null = null;
  // a session dragged onto another project (its header, or any session in it) moves there
  let dragEntry = $state<{ url: string; id: string } | null>(null);
  let dropOn = $state<string | null>(null);  // the project highlighted while a session is dragged over it
  async function moveToGroup(m: Machine, g: string) {
    const d = dragEntry;
    dragEntry = null; dropOn = null;
    const e = d && d.url === m.url ? tree[m.url]?.entries.find((x) => x.id === d.id) : null;
    if (!e || e.group === g) return;
    try { await api.updateEntry(m, e.id, { group: g }); await refresh(); say(`Moved ${e.name} to ${g}`); }
    catch (err) { say(`Couldn't move it: ${(err as Error).message}`); }
  }
  const overGroup = (ev: DragEvent, m: Machine, g: string) => {
    if (dragGroup?.url === m.url || dragEntry?.url === m.url) ev.preventDefault();
    if (dragEntry?.url === m.url) dropOn = `${m.url}|${g}`;
  };
  const dropGroupOrEntry = (ev: DragEvent, m: Machine, g: string) => { ev.preventDefault(); if (dragEntry) moveToGroup(m, g); else dropGroup(m, g); };
  async function dropGroup(m: Machine, target: string) {
    const d = dragGroup;
    dragGroup = null;
    if (!d || d.url !== m.url || d.g === target) return;
    // every project on that machine, even ones the filter hides, so dragging with a filter on loses no places
    const order = dset[m.url]?.project_order ?? [];
    const all = [...new Set((tree[m.url]?.entries ?? []).map((e) => e.group))];
    const rank = (g: string) => { const k = order.indexOf(g); return k < 0 ? 1e6 + all.indexOf(g) : k; };
    const names = all.sort((a, b) => rank(a) - rank(b)).filter((g) => g !== d.g);
    names.splice(names.indexOf(target), 0, d.g);
    const before = dset[m.url];
    dset[m.url] = { ...(before ?? { idle_close_minutes: 0 }), project_order: names };  // move it now; the daemon keeps it
    try { dset[m.url] = await api.setSettings(m, { project_order: names }); }
    catch (e) { dset[m.url] = before; say(`Couldn't save the project order on ${m.name}: ${(e as Error).message}`); }
  }

  async function open(machine: Machine, entry: Entry, how: How = 'preview') {
    if (!solo && inWindow[entry.id] === 'split') return;  // it's in view already, in the split pane
    if (!solo && inWindow[entry.id]) {
      if (await focusWindow(entry.id).catch(() => false)) return;
      delete inWindow[entry.id];  // its window is gone after all
    }
    const t = enterTab(machine, entry.id, undefined, how);
    swapDraft(entry.id);
    selected = { machine, entry };
    unread[entry.id] = false;
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
    restoreScroll(t);
  }

  // ---- tabs: sessions (and subagent views) open side by side; clicking in the tree opens a preview tab that the next
  // click replaces (as in VS Code); sending, double-clicking or Ctrl/middle-click keeps one. Remembered on this device.
  type How = 'preview' | 'keep' | 'new';
  type Tab = { url: string; entry: string; sub?: string; preview?: boolean; term?: boolean; scroll?: number | null };
  const TABS_KEY = 'switchboard.tabs';
  const tabKey = (t: { entry: string; sub?: string }) => t.entry + (t.sub ? '/' + t.sub : '');
  const savedTabs = (() => { try { return JSON.parse(localStorage.getItem(TABS_KEY) || '{}'); } catch { return {}; } })();
  let tabs = $state<Tab[]>(!solo && Array.isArray(savedTabs.tabs) ? savedTabs.tabs : []);
  let curKey = $derived(selected ? tabKey({ entry: selected.entry.id, sub: selected.sub?.id }) : null);
  $effect(() => {
    if (solo) return;
    const v = JSON.stringify({ tabs: tabs.map(({ scroll, ...t }) => t), active: curKey });
    try { localStorage.setItem(TABS_KEY, v); } catch {}
  });
  // leave the current tab (remember its scroll and terminal), then find or make the tab for what opens next
  function enterTab(machine: Machine, entry: string, sub: string | undefined, how: How) {
    const key = tabKey({ entry, sub });
    const cur = tabs.find((t) => tabKey(t) === curKey);
    if (cur && curKey !== key) {
      cur.term = termFor === cur.entry && !cur.sub;
      cur.scroll = stick || !logEl ? null : logEl.scrollTop;
    }
    let t = tabs.find((x) => tabKey(x) === key);
    if (t) { if (how !== 'preview') t.preview = false; }
    else {
      t = { url: machine.url, entry, sub, preview: how === 'preview' };
      const pi = how === 'preview' ? tabs.findIndex((x) => x.preview) : -1;
      if (pi >= 0) tabs[pi] = t;
      else { const ci = tabs.findIndex((x) => tabKey(x) === curKey); tabs.splice(ci >= 0 ? ci + 1 : tabs.length, 0, t); }
      t = tabs.find((x) => tabKey(x) === key)!;  // the reactive copy
    }
    if (curKey !== key) { termFor = t.term ? entry : null; stick = t.scroll == null; }
    return t;
  }
  function restoreScroll(t: Tab) {
    if (t.scroll == null) return;
    const y = t.scroll;
    tick().then(() => { if (logEl) logEl.scrollTop = y; });
  }
  const tabEntry = (t: Tab) => tree[t.url]?.entries.find((e) => e.id === t.entry);
  const tabMachine = (t: Tab) => machines.find((m) => m.url === t.url);
  function showTab(t: Tab) {
    const m = tabMachine(t), e = tabEntry(t);
    if (!m || !e) return closeTab(tabKey(t));
    if (t.sub) { const a = e.subagents?.find((x) => x.id === t.sub); return a ? openSub(m, e, a) : closeTab(tabKey(t)); }
    open(m, e);
  }
  function closeTab(key: string) {
    const i = tabs.findIndex((t) => tabKey(t) === key);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (key !== curKey) return;
    const next = tabs[i] ?? tabs[i - 1];
    if (next) showTab(next);
    else { swapDraft('\u0000none'); selected = null; termFor = null; }  // the closed one's draft is kept for next time
  }
  function keepTab() { const t = tabs.find((x) => tabKey(x) === curKey); if (t) t.preview = false; }
  function cycleTab(step: number) {
    if (tabs.length < 2) return;
    const i = tabs.findIndex((t) => tabKey(t) === curKey);
    showTab(tabs[(i + step + tabs.length) % tabs.length]);
  }
  // middle-click opens/closes tabs; without this Windows (WebView2) starts autoscroll on the button going down
  const noAutoscroll = (ev: MouseEvent) => { if (ev.button === 1) ev.preventDefault(); };
  let dragTab: string | null = null;
  function dropTab(target: string) {
    const from = tabs.findIndex((t) => tabKey(t) === dragTab), to = tabs.findIndex((t) => tabKey(t) === target);
    if (from < 0 || to < 0 || from === to) return;
    const [t] = tabs.splice(from, 1);
    tabs.splice(to, 0, t);
  }
  // ---- pop-out windows: a session is open in one place only, a tab here or its own window ----
  let inWindow = $state<Record<string, 'window' | 'split'>>({});  // entry ids open in their own window or the split pane (main window only)
  let onTop = $state(false);
  async function popOutCurrent() {
    const t = tabs.find((x) => tabKey(x) === curKey);
    if (t) await popOutTab(t);
  }
  // any session tab (not only the active one) into its own window, with its draft
  async function popOutTab(t: Tab) {
    const e = tabEntry(t), k = tabKey(t);
    if (!e || t.sub) return;
    const mine = k === curKey;
    const h = mine ? { text: draft, images: attachments } : drafts[e.id] ?? { text: '', images: [] };
    try { await popOut({ url: t.url, entry: e.id, ...h }, e.name); }
    catch (err) { return say(`Couldn't open a window: ${(err as Error).message ?? err}`); }
    inWindow[e.id] = 'window';
    closeTab(k);
    delete drafts[e.id];  // the draft went with it
  }
  // a tab dragged out of the window and let go over nothing pops out (as in a browser)
  function tabDragEnd(ev: DragEvent, t: Tab) {
    dragTab = null;
    if (!inShell() || solo || t.sub || !(ev.screenX || ev.screenY)) return;  // some engines report 0,0 for a cancelled drag
    const out = ev.screenX < window.screenX || ev.screenX > window.screenX + window.outerWidth
      || ev.screenY < window.screenY || ev.screenY > window.screenY + window.outerHeight;
    if (out && (ev.dataTransfer?.dropEffect ?? 'none') === 'none') popOutTab(t);
  }
  if (isDesktopApp() && !solo) onSoloEvents((id) => (inWindow[id] = 'window'), (h) => {
    delete inWindow[h.entry];
    drafts[h.entry] = { text: h.text, images: h.images as typeof attachments };
    const m = machines.find((x) => x.url === h.url), e = tree[h.url]?.entries.find((x) => x.id === h.entry);
    if (m && e) open(m, e, 'new');  // back as a tab
  });
  // a popped-out window: open its session once the tree has it, with the draft the main window handed over
  let soloReady = false;
  $effect(() => {
    if (!solo || soloReady) return;
    const m = machines.find((x) => x.url === solo.url), e = tree[solo.url]?.entries.find((x) => x.id === solo.entry);
    if (!m || !e) return;
    soloReady = true;
    const h = takeHandoff(solo.entry);
    if (h) drafts[solo.entry] = { text: h.text, images: h.images as typeof attachments };
    untrack(() => open(m, e, 'keep'));
    if (inShell() && !solo.embed) {
      soloOpened(solo.entry).catch(() => {});
      onSoloClosing(() => ({ url: solo.url, entry: solo.entry, text: draft, images: attachments }));
    }
  });
  // in the split pane: hand the draft back to the main window when it closes the pane
  if (solo?.embed && typeof window !== 'undefined') window.addEventListener('message', (ev) => {
    if (ev.source !== window.parent || ev.origin !== location.origin) return;
    if (ev.data?.type === 'give-draft')
      window.parent.postMessage({ type: 'split-draft', entry: solo.entry, text: draft, images: $state.snapshot(attachments) }, location.origin);  // a plain copy: state proxies can't be posted
    if (ev.data?.type === 'sb-shell') { useParentShell(); document.documentElement.dataset.viaParent = '1'; }  // the main window runs in the desktop app: ask it to open things
  });
  if (solo?.embed && typeof window !== 'undefined') window.parent.postMessage({ type: 'pane-ready' }, location.origin);  // the main window answers sb-shell
  // the main window does what its split pane asks (links in the browser, files on this machine, notifications)
  if (!solo && typeof window !== 'undefined') window.addEventListener('message', (ev) => {
    const d = ev.data;
    if (ev.origin !== location.origin || !splitFrame || ev.source !== splitFrame.contentWindow) return;
    if (d?.type === 'pane-ready' && inShell()) return void splitFrame.contentWindow?.postMessage({ type: 'sb-shell' }, location.origin);
    if (!d?.sb) return;
    if (d.do === 'url' && typeof d.url === 'string' && /^https?:\/\//i.test(d.url)) openExternal(d.url);
    else if (d.do === 'path' && typeof d.path === 'string') openLocal(d.path).then((r) => r === 'revealed' && say(`Shown in its folder: ${d.path}`), (e) => say(String(e)));
    else if (d.do === 'notify' && typeof d.title === 'string') notify(d.title, String(d.body ?? ''));
  });

  // ---- split view: one more session beside or below the chat (the single-session page in a pane) ----
  type SplitDir = 'side' | 'below';
  const SPLIT_KEY = 'switchboard.split';
  const splitSaved = (() => { try { return JSON.parse(localStorage.getItem(SPLIT_KEY) || '{}'); } catch { return {}; } })();
  const splitStart = !solo && splitSaved.entry ? { url: splitSaved.url as string, entry: splitSaved.entry as string } : null;
  let split = $state<{ url: string; entry: string } | null>(splitStart);
  let splitDir = $state<SplitDir>(splitSaved.dir === 'below' ? 'below' : 'side');
  let splitSize = $state<number>(typeof splitSaved.size === 'number' ? splitSaved.size : 0.5);  // the pane's share of the space
  let splitFrame = $state<HTMLIFrameElement>();
  let workEl = $state<HTMLDivElement>();
  let splitDrag = $state(false);
  if (splitStart) inWindow[splitStart.entry] = 'split';
  $effect(() => {
    if (solo) return;
    const v = JSON.stringify({ ...(split ?? {}), dir: splitDir, size: splitSize });
    try { localStorage.setItem(SPLIT_KEY, v); } catch {}
  });
  const splitEntry = $derived(split ? tree[split.url]?.entries.find((e) => e.id === split!.entry) : undefined);
  async function openSplit(m: Machine, e: Entry, dir: SplitDir) {
    if (split?.entry === e.id) { splitDir = dir; return; }
    if (split) await closeSplit();
    const k = tabKey({ entry: e.id });
    const h = k === curKey ? { text: draft, images: attachments } : drafts[e.id] ?? { text: '', images: [] };
    try { localStorage.setItem('switchboard.handoff.' + e.id, JSON.stringify({ url: m.url, entry: e.id, ...h })); } catch {}
    if (tabs.some((t) => tabKey(t) === k)) closeTab(k);
    delete drafts[e.id];  // the draft goes with it
    inWindow[e.id] = 'split';
    splitDir = dir;
    split = { url: m.url, entry: e.id };
  }
  // close the pane: its draft comes back (asked for over postMessage) and the session returns as a tab
  async function closeSplit() {
    const s = split;
    if (!s) return;
    const w = splitFrame?.contentWindow;
    const got: { text: string; images: unknown[] } | null = w ? await new Promise((ok) => {
      const done = (v: any) => { clearTimeout(t); window.removeEventListener('message', on); ok(v); };
      const on = (ev: MessageEvent) => { if (ev.origin === location.origin && ev.data?.type === 'split-draft' && ev.data.entry === s.entry) done(ev.data); };
      const t = setTimeout(() => done(null), 800);
      window.addEventListener('message', on);
      w.postMessage({ type: 'give-draft' }, location.origin);
    }) : null;
    split = null;
    delete inWindow[s.entry];
    if (got) drafts[s.entry] = { text: got.text, images: got.images as typeof attachments };
    const m = machines.find((x) => x.url === s.url), e = tree[s.url]?.entries.find((x) => x.id === s.entry);
    if (m && e) open(m, e, 'new');
  }
  function dragSplit(ev: PointerEvent) {
    const el = workEl;
    if (!el) return;
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    splitDrag = true;
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const f = splitDir === 'side' ? (r.right - e.clientX) / r.width : (r.bottom - e.clientY) / r.height;
      splitSize = Math.min(0.8, Math.max(0.2, f));
    };
    const up = () => { splitDrag = false; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // on start, reopen the tab that was active once the tree has loaded; drop tabs whose session is gone
  let tabsRestored = false;
  $effect(() => {
    if (solo || tabsRestored || !machines.length || !machines.every((m) => tree[m.url])) return;
    tabsRestored = true;
    tabs = tabs.filter((t) => tree[t.url] && (tree[t.url].error || tabEntry(t)));
    const t = untrack(() => tabs.find((x) => tabKey(x) === savedTabs.active));
    if (t && !selected) showTab(t);
  });

  // ---- subagents: running ones nest under their session in the tree; finished ones open from their Agent step ----
  // the daemon decides (0.1.9+); an older daemon doesn't send `running`
  const subRunning = (e: Entry, a: api.Sub) => LIVE_STATES.includes(e.state) && (a.running ?? (!a.finished && !a.stopped));
  // folded tree parts (project groups 'g:<machine>|<group>', subagent lists by entry id), remembered on this device
  const FOLD_KEY = 'switchboard.folded';
  let subOpen = $state<Record<string, boolean>>((() => { try { return JSON.parse(localStorage.getItem(FOLD_KEY) || '{}'); } catch { return {}; } })());
  $effect(() => { const v = JSON.stringify(subOpen); try { localStorage.setItem(FOLD_KEY, v); } catch {} });

  // ---- phone layout (a narrow window: a phone's browser now, the Android app later): one screen at a time ----
  // a narrow touch screen: not a desktop window (or split pane) that happens to be narrow
  const phoneQ = matchMedia('(max-width: 640px) and (pointer: coarse)');
  let phone = $state(phoneQ.matches || isPhoneApp());
  phoneQ.addEventListener('change', () => (phone = phoneQ.matches || isPhoneApp()));
  let phoneTab = $state<'sessions' | 'inbox'>('sessions');
  // ---- the session tree folds away: by the button, and by itself when the window is narrow ----
  // sideWant: the user's choice for a wide window (remembered); a narrow window starts folded, the button still opens it
  const SIDE_KEY = 'switchboard.side';
  const narrowQ = matchMedia('(max-width: 900px)');
  const sideSaved = (() => { try { return localStorage.getItem(SIDE_KEY) !== 'closed'; } catch { return true; } })();
  let sideWant = sideSaved;  // only read in handlers: no need for state
  let sideOpen = $state(sideSaved && !narrowQ.matches);
  narrowQ.addEventListener('change', () => (sideOpen = sideWant && !narrowQ.matches));
  function toggleSide() {
    sideOpen = !sideOpen;
    if (!narrowQ.matches) { sideWant = sideOpen; try { localStorage.setItem(SIDE_KEY, sideOpen ? 'open' : 'closed'); } catch {} }
  }
  // while folded, the button carries the tree's most urgent signal
  let sideHint = $derived(Object.values(tree).flatMap((t) => t.entries ?? []).reduce<string | null>(
    (h, e) => (h === 'waiting' || icon(e) === 'waiting' ? 'waiting' : icon(e) === 'unread' ? 'unread' : h), null));

  // ---- right-click menu on a tree entry ----
  // the menu is for a session (entry) or a project (group)
  let ctx = $state<{ x: number; y: number; machine: Machine; entry?: Entry; group?: string } | null>(null);
  function openMenu(ev: MouseEvent, machine: Machine, entry?: Entry, group?: string) {
    ev.preventDefault();
    // from the keyboard (menu key) there's no pointer position: put it under the row
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const at = ev.clientX || ev.clientY ? { x: ev.clientX, y: ev.clientY } : { x: r.left + 24, y: r.bottom };
    ctx = { ...at, machine, entry, group };
    // a menu opened near the window's edge moves back inside it (a long menu at the bottom ran off-screen)
    tick().then(() => {
      const el = document.querySelector('.ctxmenu'), c = ctx, pad = 6;
      if (!el || !c) return;
      const r = el.getBoundingClientRect();
      if (r.bottom > innerHeight - pad) c.y = Math.max(pad, innerHeight - r.height - pad);
      if (r.right > innerWidth - pad) c.x = Math.max(pad, innerWidth - r.width - pad);
    });
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
  async function openSub(machine: Machine, entry: Entry, sub: api.Sub, how: How = 'preview') {
    const t = enterTab(machine, entry.id, sub.id, how);
    termFor = null;
    swapDraft(entry.id + '/' + sub.id);
    selected = { machine, entry, sub };
    messages = [];
    loadingHistory = true;
    await loadSub();
    loadingHistory = false;
    restoreScroll(t);
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
  // opened from a pairing QR code in a browser (http://<machine>:7878/?pair=<code>): pair with that machine at once
  if (typeof location !== 'undefined' && !inShell()) {
    const code = new URLSearchParams(location.search).get('pair');
    if (code) {
      history.replaceState(null, '', location.pathname);  // the code is single-use: keep it out of the address bar
      if (!untrack(() => machines.some((m) => m.url === location.origin))) { cUrl = location.origin; cCode = code; dialog = 'connect'; tick().then(() => connect(new Event('submit'))); }
    }
  }
  // the phone app: scan the QR code a computer shows (Settings → Pair another device) and connect straight away
  async function scanToConnect() {
    cError = '';
    try {
      const p = await scanPairCode();
      if (!p) { cError = "That isn't a Switchboard pairing code."; return; }
      cUrl = p.url; cCode = p.code;
      await connect(new Event('submit'));
    } catch (e) { cError = (e as Error).message ?? String(e); }
  }
  async function connect(ev: Event) {
    ev.preventDefault();
    cError = '';
    try {
      const m = await api.pair(cUrl.trim(), cCode.trim(), 'Switchboard desktop');
      machines = [...machines.filter((x) => x.url !== m.url), m];
      syncPush();  // a new machine: subscribe this phone there too
      api.saveMachines(machines);
      dialog = null; cCode = '';
      refresh();
    } catch (e) { cError = (e as Error).message; }
  }
  function disconnect(m: Machine) {
    machines = machines.filter((x) => x.url !== m.url);
    api.saveMachines(machines);
    if (selected?.machine.url === m.url) { swapDraft('\u0000none'); selected = null; }
    tabs = tabs.filter((t) => t.url !== m.url);
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
  async function remove() { if (selected) await removeEntry(selected.machine, selected.entry); }
  // off the tree, or (forever) deleted from its machine too; its tabs close either way
  async function removeEntry(m: Machine, e: Entry, forever = false) {
    await api.removeEntry(m, e.id, forever);
    for (const t of tabs.filter((x) => x.entry === e.id && tabKey(x) !== curKey)) closeTab(tabKey(t));
    if (selected?.entry.id === e.id && curKey) closeTab(curKey);
    refresh();
  }
  // the Removed list: sessions taken off the tree, per machine, to restore or delete for good
  let removedBy = $state<Record<string, (Entry & { removed: number })[] | null>>({});
  async function openRemoved() {
    dialog = 'removed';
    for (const m of machines) api.removedEntries(m).then((v) => (removedBy[m.url] = v), () => (removedBy[m.url] = null));
  }
  async function restore(m: Machine, e: Entry) {
    try { await api.restoreEntry(m, e.id); removedBy[m.url] = (removedBy[m.url] ?? []).filter((x) => x.id !== e.id); await refresh(); say(`${e.name} is back in the tree`); }
    catch (err) { say(`Couldn't restore it: ${(err as Error).message}`); }
  }
  // delete for good: tucked at the bottom of the right-click menu, and the name has to be typed to confirm
  let doomed = $state<{ machine: Machine; entry: Entry } | null>(null);
  let doomTyped = $state('');
  let doomErr = $state('');
  async function deleteForGood(ev: Event) {
    ev.preventDefault();
    const d = doomed;
    if (!d || doomTyped.trim() !== d.entry.name.trim()) return;
    try {
      await removeEntry(d.machine, d.entry, true);
      removedBy[d.machine.url] = (removedBy[d.machine.url] ?? []).filter((x) => x.id !== d.entry.id);
      dialog = null; doomed = null; say(`Deleted ${d.entry.name} from ${d.machine.name}`);
    }
    catch (e) { const c = (e as Error).message; doomErr = c === 'open_elsewhere' ? "It's open in a terminal on that machine: close it there first." : c; }
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

{#snippet pickers()}
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
            <option value={m.value} title={m.description ?? ''}>{m.value === 'default' && m.description ? `Default · ${m.description}` : m.displayName}</option>
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
          {#each api.MODES as m}<option value={m}>{m === 'default' && dset[selected.machine.url]?.claude_default_mode && dset[selected.machine.url]?.claude_default_mode !== 'default' ? `default (${dset[selected.machine.url]?.claude_default_mode})` : m}</option>{/each}
        </select>
      </label>
{/if}
{/snippet}
{#if ctx}
  {@const mm = ctx}
  {#if mm.entry}
    {@const me = mm.entry}
    <div class="ctxmenu" role="menu" aria-label="{me.name} actions" style="left: {mm.x}px; top: {mm.y}px">
      <button role="menuitem" onclick={() => { const m = mm.machine, en = me; ctx = null; tasksFor = { machine: m, entry: en }; }}>Background tasks…{#if bgRunning(me)} <span class="muted small">({bgRunning(me)} running)</span>{/if}</button>
      {#if phone && selected?.entry.id === me.id}<button role="menuitem" onclick={() => { ctx = null; sessOpts = true; }}>Session settings…</button>{/if}
      <button role="menuitem" onclick={() => startRename('e:' + me.id, me.name)}>Rename</button>
      <button role="menuitem" disabled={!LIVE_STATES.includes(me.state)} title="Ends the Claude process; it stays in the tree and resumes on your next message"
        onclick={() => closeSession(mm.machine, me)}>Close session</button>
      {#if isDesktopApp() && !solo}<button role="menuitem" onclick={async () => { const m = mm.machine, en = me; ctx = null; await open(m, en, 'keep'); if (!inWindow[en.id]) popOutCurrent(); }}>Open in new window</button>{/if}
      {#if !solo}<button role="menuitem" onclick={() => { const m = mm.machine, en = me; ctx = null; openSplit(m, en, 'side'); }}>Open to the side</button>
        <button role="menuitem" onclick={() => { const m = mm.machine, en = me; ctx = null; openSplit(m, en, 'below'); }}>Open below</button>{/if}
      <button role="menuitem" title="Takes it off the tree; the session stays on its machine (find it under Removed to restore)"
        onclick={() => { const m = mm.machine, en = me; ctx = null; removeEntry(m, en); }}>Remove from tree</button>
      <hr />
      <button role="menuitem" class="danger" onclick={() => { doomed = { machine: mm.machine, entry: me }; ctx = null; doomTyped = ''; doomErr = ''; dialog = 'delete'; }}>Delete for good…</button>
    </div>
  {:else if mm.group !== undefined}
    {@const g = mm.group}
    <div class="ctxmenu" role="menu" aria-label="{g} project actions" style="left: {mm.x}px; top: {mm.y}px">
      <button role="menuitem" onclick={() => startRename(`g:${mm.machine.url}|${g}`, g)}>Rename project</button>
    </div>
  {/if}
{/if}

<!-- notices float at the bottom, so they show even with no session open (e.g. after deleting one) -->
{#if toast}<div class="toast small" role="status">{toast}</div>{/if}
<div class="app" class:phone class:inchat={phone && !!selected}
  class:inbtw={phone && !!selected && !selected.sub && !!btwChats[selected.entry.id] && !btwAside[selected.entry.id]} class:ininbox={phone && !selected && phoneTab === 'inbox'}>
  {#if update && !solo}
    <div class="updbar" role="status">
      <span><b>Switchboard {update.version}</b> is available{version ? ` (you have ${version})` : ''}.</span>
      {#if update.notes}<button class="link" onclick={() => (updNotes = !updNotes)}>What's new</button>{/if}
      {#if liveHere && svc?.embedded !== false && !isPhoneApp()}<span class="muted small">{liveHere} live session{liveHere === 1 ? '' : 's'} will stop and resume on your next message.</span>{/if}
      <span class="grow"></span>
      {#if updBusy !== null}<span class="muted small">Downloading… {updBusy}%</span>
      {:else}<button class="btn primary small" onclick={installUpdate}>{isPhoneApp() ? 'Download and install' : 'Restart to update'}</button>
        <button class="tiny" aria-label="Dismiss update" onclick={() => (update = null)}>Later</button>{/if}
    </div>
    {#if updNotes}<div class="updnotes md">{@html md(update.notes)}</div>{/if}
  {/if}
  <header class="titlebar" hidden={!!solo}>
    <button class="sidebtn" aria-label={sideOpen ? 'Hide sessions' : 'Show sessions'} title={sideOpen ? 'Hide sessions' : 'Show sessions'}
      aria-expanded={sideOpen} onclick={toggleSide}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><line x1="9" y1="4" x2="9" y2="20" /></svg>
      {#if !sideOpen && sideHint}<span class="st {sideHint}" title={stateLabel[sideHint]}></span>{/if}
    </button>
    <div class="brand">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--mauve)" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>
      Switchboard
    </div>
    <div class="chips">
      {#each machines as m (m.url)}
        <!-- online only once the daemon has actually answered; until then "connecting" (it may still be starting) -->
        <span class="chip" title={m.url}><span class="dot" class:on={tree[m.url] && !tree[m.url].error} class:off={!!tree[m.url]?.error}></span>{m.name}
          <span class="muted">{!tree[m.url] ? 'connecting…' : tree[m.url].error ? 'offline' : 'online'}</span>
          {#if verNote(m)}{@const n = verNote(m)!}<span class="vertag" title={n.why} aria-label={n.why}>{n.tag}</span>{/if}</span>
      {/each}
      <button class="chip dashed" onclick={() => (dialog = 'connect')}>+ Connect a machine</button>
      <button class="chip icon" class:hot={unreadCount > 0} aria-label="Inbox ({unreadCount} unread)" title="Inbox" onclick={() => (showInbox = !showInbox)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5h13L22 12v7H2v-7z" /></svg>
        {#if unreadCount}<span class="count">{unreadCount}</span>{/if}
      </button>
      <button class="chip icon" aria-label="Report a bug" title="Report a bug" onclick={() => { dialog = 'report'; rDone = ''; rReport = ''; rIssue = ''; loadGh(); }}>
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
          title="{a.type ?? 'subagent'}: {subRunning(e, a) ? 'running' : a.stopped ? 'stopped' : 'finished'}" onclick={(ev) => openSub(m, e, a, ev.ctrlKey || ev.metaKey ? 'new' : 'preview')}
          ondblclick={() => keepTab()}>
          <span class="st {subRunning(e, a) ? 'working' : 'stopped'}"></span>
          <span class="ename">↳ {a.description}</span>
          <span class="muted small">{ago(a.updated)}</span>
        </button>
        {@render subs(m, e, a.id, depth + 1)}
      {/each}
    {/snippet}
    <nav class="tree" class:folded={phone ? !!solo : !sideOpen || !!solo} aria-label="Sessions" onpointerenter={holdOrder} onpointermove={() => { if (!held) holdOrder(); }} onpointerleave={() => (held = null)}>
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
              {#if !tree[m.url]}<span class="muted small">connecting…</span>
              {:else if tree[m.url].error}<span class="offtag" title={tree[m.url].error}>can't reach</span>{/if}
              <button class="tiny" title="Disconnect" aria-label="Disconnect {m.name}" onclick={() => disconnect(m)}>×</button>
            </div>
            {#if tree[m.url]?.error}
              <p class="offnote">{m.name} isn't answering. Is it on, and is this device connected to it (Tailscale)?
                <button class="link" onclick={() => refresh()}>Retry</button></p>
            {/if}
            {#each groups(m) as [g, es] (g)}
              {@const gk = `g:${m.url}|${g}`}
              {@const gOpen = subOpen[gk] ?? true}
              {#if renaming?.key === gk}
                <div class="group"><input class="rename" aria-label="Rename project {g}" bind:value={renaming.value} {@attach focusSelect}
                  onkeydown={(ev) => renameKeys(ev, m, undefined, g)} onblur={() => commitRename(m, undefined, g)} /></div>
              {:else}
              <button class="group" class:dropon={dropOn === `${m.url}|${g}`} aria-expanded={gOpen} onclick={() => (subOpen[gk] = !gOpen)} oncontextmenu={(ev) => openMenu(ev, m, undefined, g)}
                draggable="true" title="Drag to reorder projects" ondragstart={() => (dragGroup = { url: m.url, g })} ondragend={() => (dragGroup = null)}
                ondragover={(ev) => overGroup(ev, m, g)} ondragleave={() => (dropOn = null)} ondrop={(ev) => dropGroupOrEntry(ev, m, g)}>{gOpen ? '▾' : '▸'} {g}{#if !gOpen}<span class="muted small"> · {es.length}</span>
                {@const hint = es.some((e) => icon(e) === 'waiting') ? 'waiting' : es.some((e) => icon(e) === 'unread') ? 'unread' : null}
                {#if hint}<span class="st {hint} inline" title={stateLabel[hint]}></span>{/if}{/if}</button>
              {/if}
              {#each gOpen ? es : [] as e (e.id)}
                {#if renaming?.key === 'e:' + e.id}
                <div class="entry"><span class="st {icon(e)}"></span><input class="rename" aria-label="Rename {e.name}" bind:value={renaming.value} {@attach focusSelect}
                  onkeydown={(ev) => renameKeys(ev, m, e)} onblur={() => commitRename(m, e)} /></div>
                {:else}
                <button class="entry" class:sel={selected?.entry.id === e.id && !selected.sub} class:waiting={e.state === 'waiting'} title={stateLabel[icon(e)]} onclick={(ev) => open(m, e, ev.ctrlKey || ev.metaKey ? 'new' : 'preview')} ondblclick={() => keepTab()}
                  onmousedown={noAutoscroll} onauxclick={(ev) => { if (ev.button === 1) { ev.preventDefault(); open(m, e, 'new'); } }} oncontextmenu={(ev) => openMenu(ev, m, e)}
                  draggable="true" ondragstart={(ev) => { dragEntry = { url: m.url, id: e.id }; ev.dataTransfer?.setData('text/plain', e.name); }} ondragend={() => { dragEntry = null; dropOn = null; }}
                  ondragover={(ev) => overGroup(ev, m, g)} ondrop={(ev) => { if (dragEntry) { ev.preventDefault(); moveToGroup(m, g); } }}>
                  <span class="st {icon(e)}"></span>
                  {#if e.pinned}<span class="pin" aria-label="pinned">★</span>{/if}
                  <span class="ename">{e.name}</span>
                  {#if inWindow[e.id]}<span class="muted small" title={inWindow[e.id] === 'split' ? 'Open in the split' : 'Open in its own window'}>{inWindow[e.id] === 'split' ? '◫' : '⧉'}</span>{/if}
                  {#if bgRunning(e)}<span class="bgbadge" title="{bgRunning(e)} background task{bgRunning(e) === 1 ? '' : 's'} running">⟳ {bgRunning(e)}</span>{/if}
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
        <button class="btn ghost" disabled={!machines.length} onclick={openRemoved} title="Sessions you removed from the tree: restore them or delete them for good">Removed</button>
      </div>
    </nav>

    <div class="work" class:below={splitDir === 'below'} bind:this={workEl}>
    <main class="term">
      {#if tabs.length && !solo}
        <div class="tabs" role="tablist" aria-label="Open sessions">
          {#each tabs as t (tabKey(t))}
            {@const e = tabEntry(t)}
            {#if e}
              {@const a = t.sub ? e.subagents?.find((x) => x.id === t.sub) : undefined}
              {@const name = t.sub ? `↳ ${a?.description ?? 'subagent'}` : e.name}
              {@const k = tabKey(t)}
              <div class="tab" class:on={k === curKey} class:preview={t.preview} role="presentation" draggable="true"
                ondragstart={(ev) => { dragTab = k; ev.dataTransfer?.setData('text/plain', name); if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'move'; }}
                ondragend={(ev) => tabDragEnd(ev, t)} ondragover={(ev) => ev.preventDefault()} ondrop={(ev) => { ev.preventDefault(); dropTab(k); }}
                onmousedown={noAutoscroll} onauxclick={(ev) => { if (ev.button === 1) { ev.preventDefault(); closeTab(k); } }}>
                <button class="tabname" role="tab" aria-selected={k === curKey} title={t.preview ? `${name} (preview: the next session you open replaces it; double-click to keep)` : name}
                  onclick={() => k !== curKey && showTab(t)} ondblclick={() => (t.preview = false)}>
                  <span class="st {t.sub ? (a && subRunning(e, a) ? 'working' : 'stopped') : icon(e)}"></span><span class="tlabel">{name}</span></button>
                <button class="tabx" aria-label="Close tab {name}" title="Close (Ctrl+W)" onclick={() => closeTab(k)}>×</button>
              </div>
            {/if}
          {/each}
        </div>
      {/if}
      {#if selected}
        {#if selected.sub}
        <div class="termhead mono">
          <span>subagent · {selected.sub.description}{selected.sub.type ? ` (${selected.sub.type})` : ''} · {subRunning(selected.entry, selected.sub) ? 'running' : selected.sub.stopped ? 'stopped' : 'finished'}</span>
          <span class="grow"></span>
          <button class="tiny" onclick={() => open(selected!.machine, selected!.entry)}>← {selected.entry.name}</button>
        </div>
        {:else}
        <div class="termhead mono">
          {#if phone}<button class="tiny back" aria-label="Back to sessions" onclick={() => { swapDraft('\u0000none'); selected = null; }}>‹</button>
            <span class="phonename">{selected.entry.name}</span>
            {#if btwChats[selected.entry.id]}<button class="tiny hstop" onclick={() => (btwAside[selected!.entry.id] = false)} title="Back to the side chat">/btw</button>{/if}{/if}
          <span class="cwd">{selected.machine.name}:{selected.entry.session?.cwd ?? '?'}</span>
          {#if selected.entry.session?.git_branch}<span class="branch">⎇ {selected.entry.session.git_branch}</span>{/if}
          <span class="grow"></span>
          <button class="tiny" class:on={termFor === selected.entry.id} onclick={() => { termFor = termFor === selected!.entry.id ? null : selected!.entry.id; if (!termFor) open(selected!.machine, selected!.entry); }}
            title="Switch between the chat view and the real claude terminal">{termFor === selected.entry.id ? 'Chat' : 'Terminal'}</button>
          <button class="tiny hstop" aria-label="More" title="More: background tasks, rename, split…" onclick={(ev) => { const r = (ev.currentTarget as HTMLElement).getBoundingClientRect(); ev.stopPropagation(); ctx = { x: r.right - 210, y: r.bottom + 4, machine: selected!.machine, entry: selected!.entry }; }}>⋯</button>
          {#if LIVE_STATES.includes(selected.entry.state)}<button class="tiny hstop" onclick={() => act((l, id) => l.stop(id))} title="Close the live session; it resumes on your next message">Stop</button>{/if}
          {#if solo && !solo.embed}
            <button class="tiny" class:on={onTop} aria-pressed={onTop} onclick={() => { onTop = !onTop; setOnTop(onTop).catch(() => {}); }} title="Keep this window above other windows">Keep on top</button>
            <button class="tiny" onclick={() => closeThisWindow()} title="Close this window; the session goes back to a tab in the main window">Back to tabs</button>
          {:else if !solo}
            {#if isDesktopApp()}<button class="tiny" onclick={popOutCurrent} title="Open this session in its own window">Pop out</button>{/if}
            <button class="tiny" onclick={togglePin}>{selected.entry.pinned ? 'Unpin' : 'Pin'}</button>
            <button class="tiny" onclick={remove} title="Takes it off the tree; the session stays on its machine (find it under Removed to restore)">Remove from tree</button>
          {/if}
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
                  <div class="reply"><span class="bullet">⏺</span><div class="md">{@html md(line.text)}</div>
                    <button type="button" class="copymsg" aria-label="Copy this reply" title="Copy (as markdown)" onclick={(ev) => copyText(line.text, ev.currentTarget)}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg></button></div>
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
              {:else if line.kind === 'tool' && FILE_TOOLS.includes(line.name) && line.input?.file_path}
                <!-- the path opens like one in a reply (onPathClick) -->
                <div class="step"><span class="bullet">⏺ </span><span class="tool">{line.name}(<code class="path">{line.input.file_path}</code>)</span></div>
              {:else if line.kind === 'tool'}
                <div class="step"><span class="bullet">⏺ </span><span class="tool">{bashCmd[line.id] ? line.name : line.text}</span></div>
              {:else if line.kind === 'diff'}
                {@const dl = diffLines(line)}
                <div class="diff" aria-label="{line.text}">
                  <div class="diffhead"><span class="bullet">⏺ </span>{line.text.slice(0, line.text.indexOf('(') + 1)}<code class="path">{line.file}</code>){#if line.note}<span class="muted small"> · {line.note}</span>{/if}</div>
                  {#each expanded[msg.uuid + li] ? dl : dl.slice(0, 40) as d}<div class="dl {d.t === '+' ? 'add' : d.t === '-' ? 'del' : d.t === '…' ? 'gap' : ''}">{#if dl[0]?.n !== undefined}<span class="ln">{d.n ?? ''}</span>{/if}<span class="sign">{d.t}</span>{d.s}</div>{/each}
                  {#if dl.length > 40}<button class="link small" onclick={() => (expanded[msg.uuid + li] = !expanded[msg.uuid + li])}>{expanded[msg.uuid + li] ? 'Show less' : `Show all ${dl.length} lines`}</button>{/if}
                </div>
              {:else if line.kind === 'todos'}
                <ul class="todos" aria-label="To-dos">
                  {#each line.items as t}<li class={t.status}><span class="box">{t.status === 'completed' ? '✓' : t.status === 'in_progress' ? '▸' : ''}</span>{t.status === 'in_progress' && t.activeForm ? t.activeForm : t.content}</li>{/each}
                </ul>
              {:else if 'id' in line && line.id && artifacts[line.id] && artifactUrl(line.text)}
                {@const url = artifactUrl(line.text)!}
                <button class="artcard" onclick={() => openExternal(url)} title={url}>
                  <span class="articon" aria-hidden="true">⧉</span>
                  <span class="arttext"><b>{artifacts[line.id]}</b><span class="muted small">{artifactUpdated(line.text) ? 'Updated' : 'Published'} on claude.ai · Open</span></span>
                </button>
              {:else}
                {@const rows = line.text.split('\n')}
                {@const cmd = 'id' in line && line.id ? bashCmd[line.id] : undefined}
                <div class="result">{#if cmd}<span class="cmdline"><span class="dollar">$</span>{cmd}</span>{'\n'}{/if}{expanded[msg.uuid + li] ? line.text : rows.slice(0, RESULT_LINES).join('\n')}</div>
                {#if rows.length > RESULT_LINES}<button class="link small more" onclick={() => (expanded[msg.uuid + li] = !expanded[msg.uuid + li])}>{expanded[msg.uuid + li] ? 'Show less' : `Show all ${rows.length} lines`}</button>{/if}
              {/if}
            {/each}
          {/snippet}
          {#each mainMsgs as msg (msg.uuid)}{@render lines(msg)}{/each}
          {#if !selected.sub}
          {#if thinkingLive[selected.entry.id] && !streaming[selected.entry.id]}<div class="thinking live"><span class="muted">Thinking… </span><span class="thinktext">{thinkingLive[selected.entry.id].slice(-240)}</span></div>{/if}
          <!-- drawn like the finished reply (formatted, same bubble on a phone), so it grows in place instead of jumping -->
          {#if streaming[selected.entry.id]}<div class="reply streaming"><span class="bullet">⏺</span><div class="md">{@html md(streaming[selected.entry.id])}<span class="cursor">▌</span></div></div>{/if}
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
        {#if pendingQ[selected.entry.id]?.length}
          <div class="pendingq mono" role="list" aria-label="Queued messages">
            {#each pendingQ[selected.entry.id] as m (m.uuid)}<div class="you" role="listitem"><span class="prompt">❯ </span>{msgText(m)}</div>{/each}
          </div>
        {/if}
        {#if queued[selected.entry.id]}<div class="queued muted small" aria-live="polite">{queued[selected.entry.id]} queued: sends when Claude finishes (Esc puts {queued[selected.entry.id] === 1 ? 'it' : 'them'} back here)</div>{/if}
        {#if !selected.sub && bgRunning(selected.entry)}
          <div class="bgbar" role="region" aria-label="Background tasks">
            <button type="button" class="bgtop" onclick={() => (tasksFor = { machine: selected!.machine, entry: selected!.entry })}>
              <span class="bgspin" aria-hidden="true"></span><b>{bgRunning(selected.entry)} running</b><span class="grow"></span><span class="muted">›</span>
            </button>
          </div>
        {/if}
        {#if atQ !== null && atList.length}
          <div class="cmdmenu" role="listbox" aria-label="Files">
            {#each atList as f, i (f)}
              <button type="button" role="option" aria-selected={i === atIdx} class:on={i === atIdx} onmousedown={(e) => e.preventDefault()} onclick={() => pickAt(f)}>
                <span class="mono">@{f.split('/').pop()}</span><span class="muted small">{f}</span>
              </button>
            {/each}
          </div>
        {/if}
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
            <textarea rows="1" {@attach grow} bind:value={draft} bind:this={inputEl} oninput={onDraftInput} onblur={() => setTimeout(() => (atQ = null), 150)} onpaste={onPaste} onkeydown={onInputKey} disabled={selected.entry.state === 'terminal' || selected.entry.state === 'missing'}
              placeholder={selected.entry.state === 'terminal' ? 'Open in a terminal elsewhere' : selected.entry.state === 'working' ? (phone ? 'Working… sends after' : 'Working… Enter queues, Ctrl+Enter interrupts and sends (Esc to interrupt)') : (phone ? 'Message' : 'Message this session (Shift+Enter for a new line)')}></textarea></label>
          <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden bind:this={fileInput} onchange={(e) => { addFiles((e.target as HTMLInputElement).files ?? []); (e.target as HTMLInputElement).value = ''; }} />
          <button type="button" class="tiny" aria-label="Attach images" title="Attach images (or paste / drop them)" onclick={() => fileInput?.click()}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" /></svg>
          </button>
          {#if selected.entry.state === 'working'}<button type="button" class="tiny" aria-label={phone ? 'Interrupt' : undefined} title="Interrupt (Esc)" onclick={() => act((l, id) => l.interrupt(id))}>{phone ? '■' : 'Esc · interrupt'}</button>{/if}
          {#if phone}<button type="submit" class="sendbtn" aria-label="Send" title="Send (hold for send now)"
            oncontextmenu={(ev) => { ev.preventDefault(); sendNow = true; (ev.currentTarget as HTMLButtonElement).form?.requestSubmit(); }}>↑</button>{/if}
        </form>
        {/if}
        {/if}
      {:else}
        <div class="placeholder">Pick a session from the tree, or <button class="link" disabled={!machines.length} onclick={openAdd}>add one</button>.</div>
      {/if}
    </main>
    {#if split && !solo}
      <div class="divider" role="separator" aria-label="Resize the split" aria-orientation={splitDir === 'side' ? 'vertical' : 'horizontal'} onpointerdown={dragSplit}></div>
      <section class="splitpane" style="flex-basis: {splitSize * 100}%" aria-label="Split: {splitEntry?.name ?? 'session'}">
        <div class="splithead">
          <span class="st {splitEntry ? icon(splitEntry) : 'stopped'}"></span><span class="splitname">{splitEntry?.name ?? '…'}</span>
          <span class="grow"></span>
          <button class="tiny" onclick={() => (splitDir = splitDir === 'side' ? 'below' : 'side')} title={splitDir === 'side' ? 'Stack it below instead' : 'Put it side by side instead'}
            aria-label={splitDir === 'side' ? 'Stack below' : 'Side by side'}>{splitDir === 'side' ? '⬓' : '◨'}</button>
          <button class="tiny" onclick={closeSplit} title="Close the split; the session goes back to a tab" aria-label="Close split">×</button>
        </div>
        <iframe bind:this={splitFrame} class:dragging={splitDrag} title="Split: {splitEntry?.name ?? 'session'}"
          src="/?machine={encodeURIComponent(split.url)}&solo={encodeURIComponent(split.entry)}&embed=1"></iframe>
      </section>
    {/if}
    </div>
    {#if selected && !selected.sub && btwChats[selected.entry.id] && !(phone && btwAside[selected.entry.id])}
      {@const c = btwChats[selected.entry.id]}
      {@const eid = selected.entry.id}
      <aside class="btw" aria-label="Side chat">
        <div class="btwhead">{#if phone}<button class="tiny back" aria-label="Back to the chat" title="Back to the chat (the side chat stays open)" onclick={() => (btwAside[eid] = true)}>‹</button>{/if}
          <b>Side chat</b><span class="muted small">/btw</span><span class="grow"></span>
          <button class="tiny" aria-label="Close side chat" title="Close it (nothing from it stays)" onclick={() => closeBtw(eid)}>×</button></div>
        <p class="muted small btwnote">Answers from what's been said so far. Nothing here goes into the session.</p>
        <div class="btwlog" role="log" aria-label="Side chat answers">
          {#each c.items as it, i (i)}
            {#if it.who === 'you'}<div class="you"><span class="prompt">❯ </span>{it.text}</div>
            {:else}<div class="md btwans">{@html md(it.text)}</div>{/if}
          {/each}
          {#if c.streaming}<div class="btwans">{c.streaming}<span class="cursor">▌</span></div>{:else if c.busy}<div class="muted small">Thinking…</div>{/if}
        </div>
        <form class="btwask" onsubmit={(ev) => { ev.preventDefault(); askBtw(btwDraft); btwDraft = ''; }}>
          <label class="grow"><span class="sr">Side question</span>
            <input bind:value={btwDraft} placeholder={c.items.length ? 'Ask a follow-up…' : 'Ask a side question…'} {@attach focusOnMount} /></label>
        </form>
      </aside>
    {:else if (phone ? phoneTab === 'inbox' && !selected : showInbox) && machines.length && !solo}
      <InboxView items={inboxItems} onOpen={openFromInbox} onRead={readItem} onDelete={deleteItem} onReadAll={readAll} onClose={() => (showInbox = false)} />
    {/if}
  </div>

  {#if phone && !selected && !solo}
    <nav class="phonebar" aria-label="Sections">
      <button class:on={phoneTab === 'sessions'} aria-current={phoneTab === 'sessions'} onclick={() => (phoneTab = 'sessions')}>Sessions</button>
      <button class:on={phoneTab === 'inbox'} aria-current={phoneTab === 'inbox'} onclick={() => (phoneTab = 'inbox')}>Inbox{#if unreadCount} <span class="count">{unreadCount}</span>{/if}</button>
      <button onclick={openSettings}>Settings</button>
    </nav>
  {/if}
  <footer class="status mono" hidden={!!solo?.embed || phone}>
    <span>{machines.length} machine{machines.length === 1 ? '' : 's'}</span>
    {@render pickers()}
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
      {#if isPhoneApp()}
        <button type="button" class="btn primary" onclick={scanToConnect}>Scan QR code</button>
        <p class="muted small">On the computer: Settings → Pair another device shows the code. Or type the address and code:</p>
      {:else}<p class="muted">On that machine run <code>switchboard-daemon pair</code> and enter the code it prints.</p>{/if}
      <label>Daemon address<input bind:value={cUrl} required placeholder="http://my-computer:7878" inputmode="url" autocapitalize="off" /></label>
      <label>One-time code<input bind:value={cCode} required autocomplete="off" placeholder="e.g. K7QM2XRA" use:focusOnMount /></label>
      {#if cError}<p class="error">{cError}</p>{/if}
      <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button><button class="btn primary">Connect</button></div>
    </form>
  </div>
{/if}

{#if dialog === 'watches'}
  <WatchesView {machines} entries={Object.fromEntries(Object.entries(tree).map(([u, t]) => [u, t.entries]))} onClose={() => (dialog = null)} />
{/if}

{#if sessOpts && selected}
  <div class="backdrop" role="presentation" {...backdrop(() => (sessOpts = false))}>
    <div class="dialog sheet sessopts" role="dialog" aria-label="Session settings">
      <div class="sheethead"><h2>Session settings</h2><span class="grow"></span><button type="button" class="tiny" aria-label="Close" onclick={() => (sessOpts = false)}>×</button></div>
      <p class="muted small">{selected.entry.name}: changes apply from your next message.</p>
      <div class="sessgrid">{@render pickers()}</div>
    </div>
  </div>
{/if}
{#if tasksFor && tasksEntry}
  {@const ts = bgTasks(tasksEntry)}
  {@const running = ts.filter((t) => t.status === 'running')}
  {@const done = ts.filter((t) => t.status !== 'running').reverse()}
  <div class="backdrop" role="presentation" {...backdrop(() => (tasksFor = null))}>
    <div class="dialog tasksheet" class:sheet={phone} role="dialog" aria-label="Background tasks">
      <div class="sheethead"><h2>Background tasks</h2><span class="grow"></span><button type="button" class="tiny" aria-label="Close" onclick={() => (tasksFor = null)}>×</button></div>
      <p class="muted small">{tasksEntry.name}: commands Claude runs in the background. Background agents show in the session list.</p>
      {#snippet taskRow(t: api.Task)}
        <li class="taskrow">
          {#if t.status === 'running'}<span class="bgspin" aria-label="running"></span>
          {:else}<span class={t.status === 'completed' ? 'ok' : 'bad'} aria-label={t.status}>{t.status === 'completed' ? '✓' : t.status === 'stopped' ? '■' : '✗'}</span>{/if}
          <span class="taskinfo"><span class="mono">{t.description ?? t.id}</span>
            <span class="muted small">{t.status === 'running' ? `running ${span(clock - t.started)}` : `${t.status} after ${span((t.ended ?? clock) - t.started)}`}{t.summary ? ` · ${t.summary}` : ''}</span>
            {#if t.output_file}<code class="path small" title="Open its output">{t.output_file}</code>{/if}</span>
          {#if t.status === 'running'}<button type="button" class="tiny stop" onclick={() => { try { lives.get(tasksFor!.machine.url)!.stopTask(tasksEntry!.id, t.id); } catch (err) { liveErr = (err as Error).message; } }}>Stop</button>{/if}
        </li>
      {/snippet}
      <h3 class="sub">Running</h3>
      {#if running.length}<ul class="tasklist" aria-label="Running">{#each running as t (t.id)}{@render taskRow(t)}{/each}</ul>
      {:else}<p class="muted small">Nothing running.</p>{/if}
      <h3 class="sub">Finished{done.length ? ` · ${done.length}` : ''}</h3>
      {#if done.length}<ul class="tasklist" aria-label="Finished">{#each done as t (t.id)}{@render taskRow(t)}{/each}</ul>
      {:else}<p class="muted small">None yet{LIVE_STATES.includes(tasksEntry.state) ? '' : ' (the list starts again when the session does)'}.</p>{/if}
    </div>
  </div>
{/if}

{#if dialog === 'removed'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <div class="dialog" role="dialog" aria-label="Removed sessions">
      <h2>Removed sessions</h2>
      <p class="muted small">Taken off the tree with their name, project and settings. Restore puts one back as it was (so does adding it again with + Add).</p>
      {#each machines as m (m.url)}
        {@const list = removedBy[m.url]}
        {#if machines.length > 1}<h3 class="sub">{m.name}</h3>{/if}
        {#if list === null}<p class="muted small">Needs a newer Switchboard on {m.name}.</p>
        {:else if list === undefined}<p class="muted small">Loading…</p>
        {:else if !list.length}<p class="muted small">Nothing removed{machines.length > 1 ? ` on ${m.name}` : ''}.</p>
        {:else}
          <ul class="removedlist" aria-label="Removed on {m.name}">
            {#each list as e (e.id)}
              <li><span class="rinfo"><span class="rname">{e.name}</span><span class="muted small">{e.group} · removed {ago(e.removed)} ago</span></span>
                <button type="button" class="tiny" onclick={() => restore(m, e)}>Restore</button>
                <button type="button" class="tiny danger" onclick={() => { doomed = { machine: m, entry: e }; doomTyped = ''; doomErr = ''; dialog = 'delete'; }}>Delete for good…</button></li>
            {/each}
          </ul>
        {/if}
      {/each}
      <div class="actions"><button type="button" class="btn ghost" onclick={() => (dialog = null)}>Close</button></div>
    </div>
  </div>
{/if}

{#if dialog === 'delete' && doomed}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <form class="dialog" aria-label="Delete for good" onsubmit={deleteForGood}>
      <h2>Delete "{doomed.entry.name}" for good?</h2>
      <p>This deletes the session from <b>{doomed.machine.name}</b>: its whole conversation and its subagents' transcripts. It can't be undone, and Claude Code's <code>--resume</code> won't find it either.</p>
      <p class="muted small">To only take it off the list (and keep it on the machine), use <b>Remove from tree</b> instead.</p>
      <label>Type the session's name to confirm
        <input bind:value={doomTyped} placeholder={doomed.entry.name} autocomplete="off" {@attach focusOnMount} /></label>
      {#if doomErr}<p class="error">{doomErr}</p>{/if}
      <div class="actions">
        <button type="button" class="btn ghost" onclick={() => (dialog = null)}>Cancel</button>
        <button type="submit" class="btn danger" disabled={doomTyped.trim() !== doomed.entry.name.trim()}>Delete for good</button>
      </div>
    </form>
  </div>
{/if}

{#if dialog === 'report'}
  <div class="backdrop" role="presentation" {...backdrop(() => (dialog = null))}>
    <form class="dialog" aria-label="Report a bug" onsubmit={(ev) => { if (localMachine) { ev.preventDefault(); sendToGitHub(); } else sendReport(ev); }}>
      <h2>Report a bug</h2>
      <label>What happened, and what did you expect?
        <textarea bind:value={rDesc} required rows="6" placeholder="First line becomes the title"></textarea>
      </label>
      <label class="inline"><input type="checkbox" bind:checked={rLogs} /> Include recent app and daemon logs (tokens are removed)</label>
      {#if ghCode}
        <div class="paircode" role="status" aria-label="GitHub sign-in">
          <div>Sign in to GitHub once to send reports from here. On the page that opened, enter:</div>
          <div><b class="mono big">{ghCode.user_code}</b> <span class="muted small">(copied)</span></div>
          <div class="muted small">Waiting for you to approve… <button type="button" class="link" onclick={() => openExternal(ghCode!.verification_uri)}>Open the page again</button> ·
            <button type="button" class="link" onclick={() => { ghSeq++; ghCode = null; rBusy = false; }}>Cancel</button></div>
        </div>
      {/if}
      {#if rDone}<p class="muted" role="status">{rDone}{#if rIssue} <button type="button" class="link" onclick={() => openExternal(rIssue)}>Open the issue</button>{/if}</p>{/if}
      {#if localMachine && gh?.signed_in}<p class="muted small">Sends as <b>{gh.login}</b> on GitHub.</p>{/if}
      {#if rReport}<details><summary class="muted small">Report</summary><textarea class="mono small" readonly rows="10">{rReport}</textarea></details>{/if}
      <div class="actions">
        <button type="button" class="btn ghost" onclick={() => (dialog = null)}>Close</button>
        {#if localMachine}
          <button type="button" class="btn ghost" disabled={rBusy || !rDesc.trim()} onclick={sendReport} title="Copies the report and opens a new GitHub issue in your browser: no sign-in here">Open on GitHub instead</button>
          <button class="btn primary" disabled={rBusy || !rDesc.trim()}>{rBusy ? (ghCode ? 'Waiting for GitHub…' : 'Sending…') : 'Send to GitHub'}</button>
        {:else}
          <button class="btn primary" disabled={rBusy || !rDesc.trim()}>{rBusy ? 'Collecting…' : 'Copy report & open GitHub'}</button>
        {/if}
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
      <div class="grid2">
        <label>Text size<select value={fonts.size} onchange={(e) => applyFonts({ size: +(e.currentTarget as HTMLSelectElement).value })}>
          {#each SIZES as z}<option value={z}>{z}%</option>{/each}</select></label>
        <label>Interface font<select value={fonts.ui} onchange={(e) => applyFonts({ ui: (e.currentTarget as HTMLSelectElement).value })}>
          {#each Object.entries(UI_FONTS) as [k, f]}<option value={k}>{f.label}</option>{/each}</select></label>
        <label>Chat font<select value={fonts.chat} onchange={(e) => applyFonts({ chat: (e.currentTarget as HTMLSelectElement).value })}>
          {#each Object.entries(CHAT_FONTS) as [k, f]}<option value={k}>{f.label}</option>{/each}</select></label>
        <label>Code font<select value={fonts.mono} onchange={(e) => applyFonts({ mono: (e.currentTarget as HTMLSelectElement).value })}>
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

      <h3 class="sub">Phone notifications</h3>
      <p class="muted small">The Switchboard phone app gets notifications through an <a href="https://ntfy.sh" target="_blank" rel="noreferrer">ntfy</a> server you run (on one machine is enough).</p>
      <label>Notification server<input placeholder="http://my-server:2586" bind:value={pushServer} onchange={savePushServer} inputmode="url" autocapitalize="off" /></label>
      {#if isPhoneApp()}
        <label class="toggle"><input type="checkbox" bind:checked={pushPrefs.finished} onchange={savePushPrefs} />
          <span><b>Tell me when a session finishes</b></span></label>
        <p class="muted small" aria-live="polite">{pushServer ? (pushOn ? 'This phone gets notifications.' : 'Setting up…') : 'Add a server above to get notifications.'}</p>
        <p class="muted small">What the lock screen shows follows your phone's setting (Notifications → hide sensitive content): hidden, it says only which session needs you.</p>
      {/if}

      <h3 class="sub">New sessions</h3>
      <p class="muted small">What sessions start with when you add or create them from now on (sessions already in the list keep theirs).</p>
      {#each machines as m (m.url)}
        {@const ns = dset[m.url]?.new_session ?? {}}
        {#if dset[m.url] === null}<p class="muted small">{m.name}: needs a newer Switchboard there.</p>
        {:else}
          <div class="defaults" role="group" aria-label="New sessions on {m.name}">
            <span class="dname">{m.name}</span>
            <label>Mode<select aria-label="Mode" value={ns.mode ?? ''} onchange={(e) => setDefault(m, 'mode', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Claude Code's ({dset[m.url]?.claude_default_mode ?? 'default'})</option>{#each api.MODES as md}<option value={md}>{md}</option>{/each}</select></label>
            <label>Model<select aria-label="Model" value={ns.model ?? ''} onchange={(e) => setDefault(m, 'model', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Claude Code's default</option>{#each (modelList[m.url] ?? []).filter((x) => x.value !== 'default') as md (md.value)}<option value={md.value}>{md.displayName}</option>{/each}</select></label>
            <label>Effort<select aria-label="Effort" value={ns.effort ?? ''} onchange={(e) => setDefault(m, 'effort', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">the model's default</option>{#each api.EFFORTS as ef}<option value={ef}>{ef}</option>{/each}</select></label>
            <label>Account<select aria-label="Account" value={ns.account ?? ''} onchange={(e) => setDefault(m, 'account', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">machine login</option>{#each acctList[m.url] ?? [] as a (a.name)}<option value={a.name}>{a.name}</option>{/each}</select></label>
          </div>
        {/if}
      {/each}

      <h3 class="sub">Idle sessions</h3>
      <p class="muted small">Close a live session after it has been quiet for a while, to free memory. It resumes with its history on your next message, but forgets any "Allow for this session" answers. Sessions that are working, waiting on you or running a background command stay open.</p>
      {#each machines as m (m.url)}
        <label>Close idle sessions on {m.name}
          {#if dset[m.url] === null}<span class="muted small">Needs a newer Switchboard on {m.name}.</span>
          {:else}<select value={dset[m.url]?.idle_close_minutes ?? 0} disabled={!dset[m.url]}
            onchange={(e) => setIdle(m, +(e.currentTarget as HTMLSelectElement).value)}>
            {#each IDLE as [v, label]}<option value={v}>{label}</option>{/each}</select>{/if}</label>
      {/each}

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
              <div class="pairqr" aria-label="Pairing QR code for the phone app">{@html qrSvg(pairLink(net.url ?? `http://${net.active[0]}`, code.code))}</div>
              <div class="muted small">Or scan this with the Switchboard phone app.</div>
            </div>
          {:else if code}<p class="muted small">That code expired. <button class="link" onclick={newCode}>New code</button></p>{/if}
        {/if}
        {#if net.error}<p class="error">{net.error}</p>{/if}
      {/if}
      {#if netError}<p class="error">{netError === 'only_from_this_machine' ? 'This can only be changed on that machine itself.' : netError}</p>{/if}

      {#if gh?.signed_in && localMachine}
        <h3 class="sub">Bug reports</h3>
        <p class="muted small">Signed in to GitHub as <b>{gh.login}</b>, to send bug reports from the app (it can only open issues on Switchboard's repository).
          <button class="link" onclick={async () => { gh = await api.githubSignOut(localMachine!).catch(() => gh); }}>Sign out</button></p>
      {/if}
      <h3 class="sub">Updates</h3>
      <p class="muted small">Switchboard {version || '…'}. Updates come from github.com/cyaninc-lab/switchboard and are checked for a valid signature before installing.</p>
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

  /* pinned to the window's edges rather than 100vh: under the text-size zoom, WebView2 doesn't scale vh but WebKitGTK
     does, so 100vh overflowed on Windows (the whole window scrolled) and a calc() on it broke Linux instead */
  .app { display: flex; flex-direction: column; position: fixed; inset: 0; background: var(--base);
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }  /* the phone app is drawn under the status and gesture bars */
  .titlebar[hidden] { display: none; }
  .status[hidden] { display: none; }

  /* ---- phone: one screen at a time (sessions | a chat | the inbox), chat as text messages ---- */
  .sessopts .sessgrid { display: grid; gap: 12px; margin-top: 8px; }
  .sessopts .modepick { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--sub0); }
  .sessopts .modepick select { font-size: 16px; padding: 10px; width: 100%; }
  .app.phone .chips .chip:not(.dashed), .app.phone .sidebtn, .app.phone .tabs, .app.phone .divider, .app.phone .splitpane { display: none; }
  .app.phone .titlebar { padding: 8px 12px; gap: 8px; }
  .app.phone.inchat .titlebar { display: none; }
  .app.phone .tree { width: 100%; flex: 1 1 auto; border-right: 0; }
  .app.phone.inchat .tree, .app.phone.ininbox .tree { display: none; }
  .app.phone:not(.inchat) .work { display: none; }
  .app.phone .entry { padding-top: 10px; padding-bottom: 10px; }
  .app.phone :global(aside.inbox) { width: 100%; flex: 1 1 auto; border-left: 0; }
  .app.phone .termhead { gap: 8px; padding: 8px 10px; }
  .app.phone .termhead .cwd, .app.phone .termhead > .tiny:not(.back):not(.hstop) { display: none; }
  .back { font-size: 18px; line-height: 1; padding: 2px 10px; }
  .phonename { font-family: var(--ui-font, Inter, system-ui, sans-serif); font-weight: 600; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .app.phone .log { padding: 12px; }
  .app.phone .logbody { gap: 10px; }
  .app.phone .log .you { align-self: flex-end; max-width: 82%; background: var(--mauve); color: var(--crust); border: 0; border-radius: 16px 16px 5px 16px; padding: 8px 12px; margin: 0; }
  .app.phone .log .you .prompt { display: none; }
  .app.phone .log .reply { align-self: flex-start; max-width: 88%; background: var(--s0); border-radius: 16px 16px 16px 5px; padding: 8px 12px; }
  .app.phone .log .reply > .bullet, .app.phone .log .step > .bullet { display: none; }
  .log .artcard { display: flex; align-items: center; gap: 12px; width: min(100%, 420px); margin: 2px 0 8px; padding: 10px 14px;
    text-align: left; background: var(--mantle); border: 1px solid var(--s0); border-radius: 10px; color: var(--text); cursor: pointer; }
  .log .artcard:hover { border-color: var(--mauve); }
  .log .articon { font-size: 1.4em; color: var(--mauve); }
  .log .arttext { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .log .arttext b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .app.phone .log .diff, .app.phone .log .todos, .app.phone .log details.agent {
    align-self: flex-start; max-width: 92%; border: 1px solid var(--s0); border-radius: 10px; padding: 5px 9px; font-size: 11.5px; color: var(--sub0); overflow: hidden; }
  .app.phone .inputbar { padding: 8px 10px 12px; gap: 8px; }
  .app.phone .inputbar > .prompt { display: none; }
  .app.phone .inputbar { align-items: center; }
  .app.phone .inputbar > .tiny { flex: none; width: 38px; height: 38px; border-radius: 10px; padding: 0; display: grid; place-items: center; }  /* square, like Send */
  .app.phone .log .shot { align-self: flex-end; margin: 0; max-width: 72%; max-height: 360px; border-radius: 14px; }  /* your picture, on your side */
  .app.phone .btw { width: 100%; flex: 1 1 auto; border-left: 0; }
  .app.phone.inbtw .work { display: none; }
  .sendbtn { flex: none; width: 38px; height: 38px; border-radius: 10px; border: 0; background: var(--mauve); color: var(--crust); font-weight: 700; font-size: 16px; cursor: pointer; }
  .phonebar { display: flex; border-top: 1px solid var(--s0); background: var(--mantle); }
  .phonebar button { flex: 1; background: none; border: 0; color: var(--o1); font: inherit; font-size: 13px; padding: 12px 0 16px; cursor: pointer; }
  .phonebar button.on { color: var(--mauve); font-weight: 600; }  /* in the split pane: the main window's own bar already shows these */
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
  .vertag { font-size: 11px; color: var(--peach); border: 1px solid var(--peach); border-radius: 999px; padding: 0 6px; cursor: help; }
  .chip.dashed { background: none; border-style: dashed; border-color: var(--s2); color: var(--sub0); }
  .dot { width: 8px; height: 8px; border-radius: 50%; box-sizing: border-box; }
  .dot.on { background: var(--green); } .dot.off { border: 1.5px solid var(--o1); }

  .body { flex: 1; display: flex; min-height: 0; }
  .tree { width: 290px; flex: 0 0 290px; display: flex; flex-direction: column; background: var(--mantle); border-right: 1px solid var(--s0); }
  .tree.folded { display: none; }
  .sidebtn { display: inline-flex; align-items: center; gap: 6px; background: none; border: 1px solid transparent; border-radius: 6px; padding: 3px 5px; color: var(--sub0); cursor: pointer; margin-right: -8px; }
  .sidebtn:hover { border-color: var(--s1); color: var(--text); }
  .search { display: block; padding: 12px; }
  :global(input:focus), :global(select:focus) { outline: 1px solid var(--mauve); outline-offset: 0; }
  /* scroll bars everywhere: thin, no track or arrows, a rounded thumb in the theme's surface colour (WebKitGTK and
     WebView2 both honour these; setting the standard scrollbar-width/-color on an element makes WebView2 ignore them) */
  :global(::-webkit-scrollbar) { width: 8px; height: 8px; }
  :global(::-webkit-scrollbar-track), :global(::-webkit-scrollbar-corner) { background: transparent; }
  :global(::-webkit-scrollbar-thumb) { background: var(--s1); border-radius: 99px; border: 2px solid transparent; background-clip: padding-box; }
  :global(::-webkit-scrollbar-thumb:hover) { background-color: var(--s2); }
  :global(input) { width: 100%; box-sizing: border-box; background: var(--base); border: 1px solid var(--s1); border-radius: 8px; padding: 8px 10px; color: var(--text); font: inherit; }
  .scroll { flex: 1; overflow: auto; padding: 0 8px 12px; }
  .empty { color: var(--o2); padding: 8px; }
  .machine { margin-top: 6px; } .machine.offline .group, .machine.offline .entry { opacity: 0.6; }
  .offtag { font-size: 11px; color: var(--red); border: 1px solid var(--red); border-radius: 4px; padding: 0 5px; }
  .offnote { margin: 4px 8px 8px; font-size: 12px; color: var(--sub0); }
  .mhead { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
  .mname { font-weight: 600; flex: 1; }
  :global(.tiny.on) { border-color: var(--mauve); color: var(--text); }
  :global(.tiny) { background: none; border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 2px 8px; font-size: 12px; }
  .group.dropon { background: var(--s0); outline: 1px dashed var(--mauve); border-radius: 6px; }
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
  .defaults { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin-bottom: 12px; }
  .defaults .dname { grid-column: 1 / -1; font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
  .defaults label { font-size: 12px; }
  .tasksheet { max-width: 560px; }
  .sheethead { display: flex; align-items: center; } .sheethead h2 { margin: 0; }
  .tasklist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .taskrow { display: flex; align-items: flex-start; gap: 10px; padding: 9px 11px; background: var(--mantle); border: 1px solid var(--s0); border-radius: 10px; }
  .taskrow .ok { color: var(--green); } .taskrow .bad { color: var(--red); }
  .taskinfo { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; overflow-wrap: anywhere; }
  .taskinfo code.path { background: none; padding: 0; cursor: pointer; text-decoration: underline dotted; }
  /* on a phone the list slides up from the bottom */
  .tasksheet.sheet { position: fixed; left: 0; right: 0; bottom: 0; max-width: none; width: auto; margin: 0; border-radius: 16px 16px 0 0; max-height: 85vh; overflow: auto; }
  .pairqr { width: 180px; background: #fff; border-radius: 8px; padding: 6px; margin-top: 6px; }
  .pairqr :global(svg) { display: block; width: 100%; height: auto; }
  .removedlist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .removedlist li { display: flex; align-items: center; gap: 10px; padding: 8px 10px; background: var(--mantle); border: 1px solid var(--s0); border-radius: 8px; }
  .removedlist .rinfo { display: flex; flex-direction: column; min-width: 0; flex: 1; }
  .removedlist .rname { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .removedlist button { white-space: nowrap; flex: none; } .tiny.danger { color: var(--red); border-color: color-mix(in srgb, var(--red) 45%, transparent); }
  /* /btw side chat: where the inbox is */
  .btw { width: 340px; flex: 0 0 340px; display: flex; flex-direction: column; background: var(--mantle); border-left: 1px solid var(--s0); min-height: 0; }
  .btwhead { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--s0); }
  .btwnote { margin: 8px 12px 0; }
  .btwlog { flex: 1; overflow: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; font-family: var(--chat-font, Inter, system-ui, sans-serif); }
  .btwans { white-space: pre-wrap; } .btwans.md { white-space: normal; }
  .btwask { padding: 10px 12px; border-top: 1px solid var(--s0); display: flex; }
  .ctxmenu hr { border: 0; border-top: 1px solid var(--s0); margin: 4px 2px; }
  .ctxmenu button.danger { color: var(--red); }
  :global(.btn.danger) { background: var(--red); color: var(--crust); border-color: var(--red); font-weight: 600; }
  :global(.btn.danger:disabled) { opacity: 0.45; }
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

  /* the chat and, when split, a second session beside (side) or below it */
  .work { flex: 1; min-width: 0; min-height: 0; display: flex; }
  .work.below { flex-direction: column; }
  .work > .term { flex: 1 1 0; min-height: 0; }
  .divider { flex: none; width: 5px; cursor: col-resize; background: var(--s0); }
  .work.below .divider { width: auto; height: 5px; cursor: row-resize; }
  .divider:hover { background: var(--mauve); }
  .splitpane { flex: 0 0 50%; display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--base); }
  .splithead { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--mantle); border-bottom: 1px solid var(--s0); font-size: 13px; }
  .splitname { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .splitpane iframe { flex: 1; border: 0; width: 100%; background: var(--base); }
  .splitpane iframe.dragging { pointer-events: none; }  /* so a resize drag isn't swallowed by the pane */
  .term { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--base); }
  /* the bottom line is drawn inside the bar (inset shadow) so the active tab can cover it without overflowing the bar,
     which made Windows show a vertical scrollbar */
  .tabs { display: flex; gap: 2px; padding: 6px 8px 0; background: var(--mantle); box-shadow: inset 0 -1px 0 var(--s0); overflow-x: auto; overflow-y: hidden; flex: none; }
  .tab { display: flex; align-items: center; max-width: 220px; min-width: 0; border: 1px solid transparent; border-bottom: 0; border-radius: 8px 8px 0 0; color: var(--sub0); }
  .tab:hover { background: var(--s0); }
  .tab.on { background: var(--base); border-color: var(--s0); color: var(--text); }
  .tab.preview .tlabel { font-style: italic; }
  .tabname { display: flex; align-items: center; gap: 8px; min-width: 0; background: none; border: 0; color: inherit; font: inherit; font-size: 13px; padding: 7px 4px 7px 10px; cursor: pointer; }
  .tlabel { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tabx { background: none; border: 0; color: var(--o1); cursor: pointer; padding: 2px 8px 2px 4px; font-size: 14px; line-height: 1; border-radius: 4px; }
  .tabx:hover { color: var(--text); }
  .tab:not(.on):not(:hover) .tabx { visibility: hidden; }
  .termhead { display: flex; gap: 16px; align-items: center; padding: 8px 18px; font-size: 12px; color: var(--o2); border-bottom: 1px solid var(--s0); }
  .logbody { display: flex; flex-direction: column; gap: 8px; }
  .log { flex: 1; overflow: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  /* your messages: a soft mauve band with an edge, so they stand out when scrolling back */
  .you { color: var(--text); background: color-mix(in srgb, var(--mauve) 10%, transparent); border-left: 3px solid var(--mauve); border-radius: 6px; padding: 8px 12px; margin: 4px 0; }
  .reply { display: flex; gap: 8px; white-space: normal; position: relative; }
  /* copy: a reply's button shows on hover (or focus); a code block's sits in its corner */
  .copymsg { position: absolute; top: 0; right: 0; background: var(--mantle); border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 3px 5px; line-height: 0; cursor: pointer; opacity: 0; }
  .reply:hover .copymsg, .copymsg:focus-visible { opacity: 1; }
  .copymsg:hover { color: var(--text); } :global(.copymsg.copied) { color: var(--green); border-color: var(--green); opacity: 1; }
  .md :global(pre) { position: relative; padding-right: 64px; }  /* room for the Copy button */
  .md :global(pre > button.copy) { position: absolute; top: 6px; right: 6px; font: inherit; font-size: 11px; background: var(--s0); border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 2px 8px; cursor: pointer; opacity: 0.7; }
  .md :global(pre:hover > button.copy) { opacity: 1; } .md :global(pre > button.copy:hover) { color: var(--text); }
  .md :global(pre > button.copied) { color: var(--green); border-color: var(--green); }
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
  .dl .ln { display: inline-block; min-width: 3.5ch; margin-right: 8px; text-align: right; color: var(--o1); user-select: none; }
  .dl.gap { color: var(--o1); height: 0.9em; } .dl.gap .sign { visibility: hidden; }
  .dl.add { background: color-mix(in srgb, var(--green) 12%, transparent); color: var(--green); } .dl.del { background: color-mix(in srgb, var(--red) 12%, transparent); color: var(--red); }
  .diff .link { align-self: flex-start; margin: 4px 10px; }
  .todos { list-style: none; margin: 0; padding: 6px 10px; border: 1px solid var(--s0); border-radius: 8px; display: flex; flex-direction: column; gap: 2px; font-family: var(--ui-font, Inter, system-ui, sans-serif); white-space: normal; }
  .todos .box { display: inline-block; width: 14px; height: 14px; border: 1px solid var(--s2); border-radius: 3px; margin-right: 8px; text-align: center; line-height: 13px; font-size: 11px; vertical-align: -2px; }
  .todos li.completed { color: var(--o2); text-decoration: line-through; } .todos li.completed .box { color: var(--green); border-color: var(--green); }
  .todos li.in_progress { color: var(--text); font-weight: 600; } .todos li.in_progress .box { color: var(--peach); border-color: var(--peach); }
  .inputbar { display: flex; gap: 10px; align-items: center; padding: 12px 18px; border-top: 1px solid var(--s0); }
  .inputbar { align-items: flex-end; }
  /* background tasks: a slim bar on top of the message box */
  .bgbar { margin: 0 18px; border: 1px solid var(--s1); border-bottom: 0; border-radius: 8px 8px 0 0; background: var(--mantle); font-size: 12px; }
  .bgtop { display: flex; align-items: center; gap: 6px; width: 100%; background: none; border: 0; color: var(--sky); font: inherit; padding: 6px 10px; cursor: pointer; text-align: left; }
  .bgrow { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 6px 10px; border-top: 1px solid var(--s0); }
  .bgdesc { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bgrow code.path { font-size: 11px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; text-decoration: underline dotted; background: none; }
  .bgrow .ok { color: var(--green); } .bgrow .bad { color: var(--red); }
  .tiny.stop { color: var(--red); border-color: color-mix(in srgb, var(--red) 45%, transparent); }
  .bgspin { display: inline-block; width: 9px; height: 9px; border: 2px solid var(--sky); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
  .bgbadge { font-size: 10px; font-weight: 600; color: var(--crust); background: var(--sky); border-radius: 99px; padding: 0 6px; }
  @media (prefers-reduced-motion: reduce) { .bgspin { animation: none; } }
  .toast { position: fixed; left: 50%; bottom: 76px; transform: translateX(-50%); z-index: 60; max-width: min(70vw, 720px); word-break: break-all;
    background: var(--s0); color: var(--text); border: 1px solid var(--s1); border-radius: 8px; padding: 6px 12px; box-shadow: 0 4px 16px rgb(0 0 0 / 0.25); }
  /* paths in step headers (Read(...), Edit(...)): clickable like those in replies, without the code pill */
  .tool code.path, .diffhead code.path { background: none; padding: 0; font-size: inherit; cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
  .tool code.path:hover, .diffhead code.path:hover { color: var(--sky); }
  .md :global(code.path) { cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
  .md :global(code.path:hover) { color: var(--sky); }
  .pendingq { padding: 8px 18px 0; display: flex; flex-direction: column; gap: 4px; opacity: 0.75; white-space: pre-wrap; max-height: 25vh; overflow-y: auto; }
  .inputbar textarea { display: block; width: 100%; box-sizing: border-box; border: 0; background: none; color: var(--text); font: inherit; padding: 8px 10px; resize: none; max-height: 40vh; overflow-y: auto; line-height: 1.45; }
  .inputbar textarea:focus { outline: 1px solid var(--mauve); border-radius: 8px; }
  .inputbar .prompt { padding-bottom: 8px; }
  .placeholder { margin: auto; color: var(--o2); }
  /* the chat font: Claude's replies (streaming and finished), thinking, your messages, messages between sessions and
     the message box; code inside them keeps the code font (.md code) */
  .log .md, .streaming, .thinking, .you, .mail, .inputbar textarea { font-family: var(--chat-font, Inter, system-ui, sans-serif); }
  /* steps as little terminal windows (desktop and phone): a title bar with coloured dots and the tool, the output
     below; a Bash window starts with "$ <command>" */
  .log .step { align-self: flex-start; box-sizing: border-box; max-width: 100%; width: min(100%, 760px); background: var(--s0); border: 1px solid var(--s1);
    border-radius: 10px; padding: 6px 10px; font-size: 11.5px; color: var(--sub1); overflow: hidden; text-overflow: ellipsis; }
  .log .step > .bullet { display: none; }
  .log .step::before { content: ''; display: inline-block; width: 8px; height: 8px; border-radius: 50%; vertical-align: 1px;
    background: var(--red); box-shadow: 13px 0 0 var(--peach), 26px 0 0 var(--green); margin-right: 36px; }
  .log .step .tool { color: var(--text); }
  .log .step:has(+ .result) { border-radius: 10px 10px 0 0; border-bottom: 0; }
  .log .result { align-self: flex-start; box-sizing: border-box; max-width: 100%; width: min(100%, 760px); background: var(--crust); border: 1px solid var(--s1); color: var(--sub0);
    border-radius: 10px; padding: 7px 10px 9px; font-size: 12px; overflow-x: auto; }
  .log .step + .result { margin-top: -8px; border-top: 0; border-radius: 0 0 10px 10px; }  /* the gap between lines is 8px */
  .log .result .cmdline { color: var(--text); } .log .result .dollar { color: var(--o1); margin-right: 1ch; }
  .app.phone .log .step, .app.phone .log .result { width: 92%; }
  .app.phone .log .step + .result { margin-top: -10px; }
  .app.phone .termhead .branch { display: none; }  /* a long name and the branch wrapped onto two lines */
  .streaming .md > :global(p:nth-last-child(2)) { display: inline; }  /* the cursor stays at the end of the last line */
  .streaming { color: var(--text); } .cursor { color: var(--mauve); animation: blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .perm { border: 1px solid color-mix(in srgb, var(--peach) 45%, transparent); background: color-mix(in srgb, var(--peach) 8%, transparent); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; white-space: normal; }
  .permtitle { color: var(--peach); font-weight: 600; }
  .permactions { display: flex; gap: 8px; font-family: var(--ui-font, Inter, system-ui, sans-serif); }
  .allow { background: var(--green); color: var(--crust); border: 0; border-radius: 8px; padding: 8px 16px; font-weight: 600; }
  .deny { background: none; color: var(--red); border: 1px solid color-mix(in srgb, var(--red) 45%, transparent); border-radius: 8px; padding: 8px 16px; }
  /* its own shape: in the chat's column it was stretched to the full width while its height was capped (squashed) */
  .shot { align-self: flex-start; width: auto; height: auto; max-width: min(320px, 100%); max-height: 200px; object-fit: contain; border-radius: 8px; border: 1px solid var(--s1); display: block; margin: 4px 0 4px 18px; }
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
