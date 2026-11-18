<script module lang="ts">
  // half-given answers outlive the card (switching sessions unmounts it), keyed by request id
  // ponytail: never pruned; a few small objects per question asked in this window
  const drafts: Record<string, { picked: Record<string, string[]>; other: Record<string, string>; feedback: string }> = $state({});
</script>

<script lang="ts">
  // One thing Claude is waiting on: a clarifying question, a plan to approve, or permission to use a tool.
  import type { Live } from './api';
  import { md } from './markdown';
  import { untrack } from 'svelte';

  type P = { request_id: string; tool: string; kind: 'tool' | 'question' | 'plan'; input: any; remember: string[] };
  let { p, live }: { p: P; live: Live } = $props();
  const summary = (i: any) => i?.command ?? i?.file_path ?? i?.pattern ?? i?.url ?? JSON.stringify(i).slice(0, 200);

  // questions
  const id = untrack(() => p.request_id);  // the parent keys cards by request id: it never changes for this card
  drafts[id] ??= { picked: {}, other: {}, feedback: '' };
  const d = drafts[id];
  const picked = d.picked, other = d.other;
  const questions: any[] = $derived(p.input?.questions ?? []);
  function toggle(q: any, label: string) {
    const cur = picked[q.question] ?? [];
    picked[q.question] = q.multiSelect ? (cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label]) : [label];
    other[q.question] = '';
    // one single-choice question: a click is the answer, as in Claude Code
    if (questions.length === 1 && !q.multiSelect) submitAnswers();
  }
  const answered = $derived(questions.every((q) => (picked[q.question]?.length ?? 0) > 0 || (other[q.question] ?? '').trim()));
  function submitAnswers() {
    const answers: Record<string, string | string[]> = {};
    for (const q of questions) {
      const own = (other[q.question] ?? '').trim();
      answers[q.question] = own || (q.multiSelect ? picked[q.question] ?? [] : (picked[q.question] ?? [])[0] ?? '');
    }
    live.answer(p.request_id, true, { answers });
  }

  // plans
</script>

{#if p.kind === 'question'}
  <div class="card q" role="group" aria-label="Claude asks">
    {#each questions as q (q.question)}
      <div class="qhead"><span class="chip">{q.header}</span><span>{q.question}</span></div>
      <div class="opts" role={q.multiSelect ? 'group' : 'radiogroup'} aria-label={q.question}>
        {#each q.options as o (o.label)}
          <button role={q.multiSelect ? 'checkbox' : 'radio'} aria-checked={(picked[q.question] ?? []).includes(o.label)}
            class:on={(picked[q.question] ?? []).includes(o.label)} onclick={() => toggle(q, o.label)}>
            <b>{o.label}</b>{#if o.description}<span>{o.description}</span>{/if}
          </button>
        {/each}
      </div>
      <label class="own"><span class="sr">Other answer to: {q.question}</span>
        <input placeholder="Other: type your own answer" bind:value={other[q.question]} oninput={() => (picked[q.question] = [])}
          onkeydown={(e) => e.key === 'Enter' && answered && submitAnswers()} /></label>
    {/each}
    <div class="row">
      {#if questions.length > 1 || questions.some((q) => q.multiSelect) || Object.values(other).some((v) => v?.trim())}
        <button class="primary" disabled={!answered} onclick={submitAnswers}>Answer</button>
      {/if}
      <button class="quiet" onclick={() => live.answer(p.request_id, false, { message: 'The user dismissed the question.' })}>Dismiss</button>
    </div>
  </div>
{:else if p.kind === 'plan'}
  <div class="card plan" role="group" aria-label="Plan to approve">
    <div class="title">Claude's plan</div>
    <div class="md">{@html md(p.input?.plan ?? '')}</div>
    <div class="row">
      <button class="primary" onclick={() => live.answer(p.request_id, true)}>Approve plan</button>
      <label class="grow"><span class="sr">What to change in the plan</span>
        <input placeholder="Or say what to change…" bind:value={d.feedback} onkeydown={(e) => e.key === 'Enter' && live.answer(p.request_id, false, { message: d.feedback })} /></label>
      <button class="quiet" onclick={() => live.answer(p.request_id, false, { message: d.feedback })}>Keep planning</button>
    </div>
  </div>
{:else}
  <div class="card perm" role="group" aria-label="Permission needed">
    <div class="title">Permission needed</div>
    <div class="mono">{p.tool}: {summary(p.input)}</div>
    <div class="row">
      <button class="allow" onclick={() => live.answer(p.request_id, true)}>Allow</button>
      {#if p.remember.includes('session')}<button class="quiet" onclick={() => live.answer(p.request_id, true, { remember: 'session' })}>Allow for this session</button>{/if}
      {#if p.remember.includes('localSettings')}<button class="quiet" title="Saves a rule in this project's .claude/settings.local.json" onclick={() => live.answer(p.request_id, true, { remember: 'localSettings' })}>Always allow</button>{/if}
      <button class="deny" onclick={() => live.answer(p.request_id, false)}>Deny</button>
    </div>
  </div>
{/if}

<style>
  .card { border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; white-space: normal; font-family: Inter, system-ui, sans-serif; }
  .perm { border: 1px solid rgba(250, 179, 135, 0.45); background: rgba(250, 179, 135, 0.08); }
  .q { border: 1px solid rgba(137, 220, 235, 0.45); background: rgba(137, 220, 235, 0.07); }
  .plan { border: 1px solid rgba(203, 166, 247, 0.45); background: rgba(203, 166, 247, 0.07); }
  .title { font-weight: 600; color: var(--peach); } .plan .title { color: var(--mauve); }
  .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
  .qhead { display: flex; gap: 8px; align-items: baseline; }
  .chip { font-size: 11px; color: var(--sky); border: 1px solid rgba(137, 220, 235, 0.5); border-radius: 999px; padding: 1px 8px; }
  .opts { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 6px; }
  .opts button { display: flex; flex-direction: column; gap: 2px; text-align: left; background: var(--mantle); border: 1px solid var(--s1); color: var(--text); border-radius: 8px; padding: 8px 10px; cursor: pointer; }
  .opts button.on { border-color: var(--sky); background: var(--s0); }
  .opts span { font-size: 11px; color: var(--sub0); }
  .own input { margin-top: 2px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .grow { flex: 1; min-width: 160px; }
  .row button { border-radius: 8px; padding: 7px 14px; cursor: pointer; font: inherit; }
  .primary, .allow { background: var(--green); color: var(--crust); border: 0; font-weight: 600; }
  .primary:disabled { opacity: 0.5; }
  .quiet { background: none; color: var(--text); border: 1px solid var(--s1); }
  .deny { background: none; color: var(--red); border: 1px solid rgba(243, 139, 168, 0.45); }
  .md :global(p) { margin: 0 0 6px; } .md :global(h2) { font-size: 13px; margin: 0 0 6px; color: var(--mauve); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
