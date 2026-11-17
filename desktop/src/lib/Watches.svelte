<script lang="ts">
  // Watches on a machine: things its daemon checks on its own; when one fires it notifies you or wakes an agent.
  import * as api from './api';
  import type { Machine, Entry, Watch, WatchKind } from './api';

  let { machines, entries, onClose }: { machines: Machine[]; entries: Record<string, Entry[]>; onClose: () => void } = $props();
  let machine = $state<Machine | null>(null);
  $effect(() => { if (!machine || !machines.some((m) => m.url === machine!.url)) machine = machines[0] ?? null; });
  let list = $state<Watch[]>([]);
  let editing = $state<Partial<Watch> | null>(null);
  let error = $state('');
  let note = $state('');

  const KINDS: { kind: WatchKind; label: string; hint: string }[] = [
    { kind: 'schedule', label: 'Schedule', hint: 'Every N minutes, or daily at a time' },
    { kind: 'command', label: 'Command', hint: "When a command's output changes, or it starts failing" },
    { kind: 'http', label: 'HTTP check', hint: 'When an address goes down or comes back' },
    { kind: 'github', label: 'GitHub', hint: "New issues, PRs or failed runs (uses this machine's gh login)" },
  ];
  const errors: Record<string, string> = {
    name_required: 'Give it a name', bad_kind: 'Pick a kind', schedule_needs_every_minutes_or_daily_at: 'Set minutes or a daily time',
    'daily_at_must_be_HH:MM': 'Daily time must be HH:MM', command_required: 'Enter a command', url_must_be_http: 'The address must start with http:// or https://',
    'repo_must_be_owner/name': 'Repository must be owner/name', wake_needs_an_entry: 'Pick the session to wake',
  };

  async function load() { if (machine) try { list = await api.watches(machine); } catch (e) { error = (e as Error).message; } }
  $effect(() => { machine; load(); });

  function blank(): Partial<Watch> {
    return { name: '', kind: 'schedule', config: { every_minutes: 60 }, action: 'notify', entry_id: null, instructions: '', mode: null, enabled: true };
  }
  function pickKind(k: WatchKind) {
    if (!editing) return;
    editing.kind = k;
    editing.config = k === 'schedule' ? { every_minutes: 60 } : k === 'command' ? { command: '', every_minutes: 15, fire_on: 'change' }
      : k === 'http' ? { url: 'https://', every_minutes: 5 } : { repo: '', events: ['issues', 'pulls', 'failed_runs'], every_minutes: 10 };
  }
  async function save(ev: Event) {
    ev.preventDefault();
    if (!machine || !editing) return;
    try {
      const w = { ...editing, entry_id: editing.action === 'wake' ? editing.entry_id : null, mode: editing.mode || null };
      if (w.kind === 'schedule' && w.config?.daily_at) delete w.config.every_minutes;
      await api.saveWatch(machine, w);
      editing = null; error = '';
      load();
    } catch (e) { error = errors[(e as Error).message] ?? (e as Error).message; }
  }
  async function toggle(w: Watch) { if (machine) { await api.saveWatch(machine, { id: w.id, enabled: !w.enabled }); load(); } }
  async function remove(w: Watch) { if (machine) { await api.removeWatch(machine, w.id); load(); } }
  async function test(w: Watch) {
    if (!machine) return;
    note = `Testing "${w.name}"…`;
    try { await api.testWatch(machine, w.id); note = `"${w.name}" fired: check the inbox${w.action === 'wake' ? ' (the agent replies there when it finishes)' : ''}.`; load(); }
    catch (e) { note = `Test failed: ${(e as Error).message}`; }
  }
  const describe = (w: Watch) => {
    const c = w.config;
    const when = c.daily_at ? `daily at ${c.daily_at}` : `every ${c.every_minutes ?? 15} min`;
    const what = w.kind === 'command' ? `$ ${c.command}` : w.kind === 'http' ? c.url : w.kind === 'github' ? `${c.repo}: ${(c.events ?? []).join(', ')}` : 'scheduled';
    return `${what} · ${when}`;
  };
  const entryName = (id: string | null) => (machine && id ? entries[machine.url]?.find((e) => e.id === id)?.name : null) ?? 'a session';
  const ago = (s: number | null) => {
    if (!s) return 'never';
    const m = Math.round((Date.now() / 1000 - s) / 60);
    return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
  };
</script>

<div class="backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && onClose()}>
  <div class="dialog wide" role="dialog" aria-label="Watches">
    <h2>Watches</h2>
    <p class="muted small">Each machine's daemon runs its own watches. To keep them running with the app closed, turn on the background service in Settings.</p>
    <div class="pills">
      {#each machines as m (m.url)}<button type="button" class="pill" class:on={machine?.url === m.url} onclick={() => { machine = m; editing = null; }}>{m.name}</button>{/each}
    </div>

    {#if !editing}
      <ul class="wlist" aria-label="Watches on {machine?.name}">
        {#each list as w (w.id)}
          <li class:off={!w.enabled}>
            <div class="wmain">
              <b>{w.name}</b>
              <span class="muted small mono">{describe(w)}</span>
              <span class="muted small">{w.action === 'wake' ? `wakes ${entryName(w.entry_id)}` : 'notifies you'} · last fired {ago(w.last_fired)}</span>
            </div>
            <div class="wbtns">
              <button class="tiny" onclick={() => test(w)}>Test</button>
              <button class="tiny" onclick={() => (editing = structuredClone($state.snapshot(w)))}>Edit</button>
              <button class="tiny" onclick={() => toggle(w)}>{w.enabled ? 'Pause' : 'Resume'}</button>
              <button class="tiny" aria-label="Delete {w.name}" onclick={() => remove(w)}>Delete</button>
            </div>
          </li>
        {:else}
          <li class="muted small">No watches on {machine?.name} yet.</li>
        {/each}
      </ul>
      {#if note}<p class="muted small" role="status">{note}</p>{/if}
      <div class="actions"><button class="btn ghost" onclick={onClose}>Close</button><button class="btn primary" onclick={() => (editing = blank())}>New watch</button></div>
    {:else}
      <form class="wform" aria-label="Edit watch" onsubmit={save}>
        <label>Name<input bind:value={editing.name} placeholder="e.g. Deploy failures" /></label>
        <div class="pills" role="radiogroup" aria-label="Kind">
          {#each KINDS as k}<button type="button" role="radio" aria-checked={editing.kind === k.kind} class="pill" class:on={editing.kind === k.kind} title={k.hint} onclick={() => pickKind(k.kind)}>{k.label}</button>{/each}
        </div>
        {#if editing.config}
          {#if editing.kind === 'schedule'}
            <div class="grid2">
              <label>Every (minutes)<input type="number" min="1" bind:value={editing.config.every_minutes} disabled={!!editing.config.daily_at} /></label>
              <label>…or daily at<input type="time" bind:value={editing.config.daily_at} /></label>
            </div>
          {:else if editing.kind === 'command'}
            <label>Command<input class="mono" bind:value={editing.config.command} placeholder="systemctl --user is-failed myapp" /></label>
            <div class="grid2">
              <label>Every (minutes)<input type="number" min="1" bind:value={editing.config.every_minutes} /></label>
              <label>Fire when<select bind:value={editing.config.fire_on}><option value="change">the output changes</option><option value="failure">it starts failing</option></select></label>
            </div>
          {:else if editing.kind === 'http'}
            <label>Address<input bind:value={editing.config.url} /></label>
            <label>Every (minutes)<input type="number" min="1" bind:value={editing.config.every_minutes} /></label>
          {:else}
            <label>Repository<input bind:value={editing.config.repo} placeholder="owner/name" /></label>
            <div class="checks">
              {#each [['issues', 'new issues'], ['pulls', 'new PRs'], ['failed_runs', 'failed workflow runs']] as [v, l]}
                <label class="inline"><input type="checkbox" checked={editing.config.events?.includes(v)}
                  onchange={(e) => { const on = (e.target as HTMLInputElement).checked; editing!.config!.events = on ? [...(editing!.config!.events ?? []), v] : editing!.config!.events.filter((x: string) => x !== v); }} /> {l}</label>
              {/each}
            </div>
            <label>Every (minutes)<input type="number" min="1" bind:value={editing.config.every_minutes} /></label>
          {/if}
        {/if}
        <div class="pills" role="radiogroup" aria-label="When it fires">
          <button type="button" role="radio" aria-checked={editing.action === 'notify'} class="pill" class:on={editing.action === 'notify'} onclick={() => (editing!.action = 'notify')}>Just notify me</button>
          <button type="button" role="radio" aria-checked={editing.action === 'wake'} class="pill" class:on={editing.action === 'wake'} onclick={() => (editing!.action = 'wake')}>Wake an agent</button>
        </div>
        {#if editing.action === 'wake'}
          <div class="grid2">
            <label>Session to wake<select bind:value={editing.entry_id}>
              <option value={null}>pick one…</option>
              {#each machine ? entries[machine.url] ?? [] : [] as e (e.id)}<option value={e.id}>{e.name}</option>{/each}
            </select></label>
            <label>Permission mode while woken<select bind:value={editing.mode}>
              <option value={null}>the session's own</option>{#each api.MODES as m}<option value={m}>{m}</option>{/each}
            </select></label>
          </div>
          <label>Instructions for the agent<textarea rows="3" bind:value={editing.instructions} placeholder="Look into it and tell me what matters, briefly."></textarea></label>
        {/if}
        {#if error}<p class="error">{error}</p>{/if}
        <div class="actions"><button type="button" class="btn ghost" onclick={() => { editing = null; error = ''; }}>Cancel</button><button class="btn primary">Save watch</button></div>
      </form>
    {/if}
  </div>
</div>

<style>
  .wlist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .wlist li { display: flex; gap: 10px; align-items: center; padding: 10px 12px; background: var(--mantle); border: 1px solid var(--s0); border-radius: 10px; }
  .wlist li.off { opacity: 0.6; }
  .wmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .wmain .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wbtns { display: flex; gap: 6px; }
  .wform { display: flex; flex-direction: column; gap: 10px; }
  .checks { display: flex; gap: 16px; flex-wrap: wrap; }
</style>
