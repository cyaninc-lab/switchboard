<script lang="ts">
  // What watches and woken agents have to tell you, from every connected machine, newest first.
  import type { InboxItem } from './api';
  import { md } from './markdown';

  type Item = InboxItem & { machine: string; url: string };
  let { items, onOpen, onRead, onReadAll, onClose }: {
    items: Item[]; onOpen: (i: Item) => void; onRead: (i: Item) => void; onReadAll: () => void; onClose: () => void;
  } = $props();
  let unread = $derived(items.filter((i) => !i.read).length);
  const when = (s: number) => {
    const d = (Date.now() / 1000 - s);
    return d < 60 ? 'now' : d < 3600 ? `${Math.round(d / 60)}m` : d < 86400 ? `${Math.round(d / 3600)}h` : `${Math.round(d / 86400)}d`;
  };
</script>

<aside class="inbox" aria-label="Inbox">
  <div class="head">
    <b>Inbox</b>
    {#if unread}<span class="badge">{unread} new</span>{/if}
    <span class="grow"></span>
    {#if unread}<button class="tiny" onclick={onReadAll}>Mark all read</button>{/if}
    <button class="tiny" aria-label="Close inbox" onclick={onClose}>×</button>
  </div>
  <div class="list">
    {#if !items.length}<p class="muted small empty">Nothing yet. Watches and the agents they wake post here.</p>{/if}
    {#each items as i (i.url + i.id)}
      <article class="item" class:unread={!i.read} aria-label="{i.source}: {i.title}">
        <div class="meta">
          <span class="dot" class:agent={i.from_agent}></span>
          <b>{i.source}</b><span class="muted">· {i.machine}</span>
          <span class="grow"></span><span class="muted small">{when(i.created)}</span>
        </div>
        <div class="title">{i.from_agent ? 'Agent:' : ''} {i.title}</div>
        <div class="body md">{@html md(i.body.length > 1200 ? i.body.slice(0, 1200) + '…' : i.body)}</div>
        <div class="actions">
          {#if i.entry_id}<button class="tiny" onclick={() => onOpen(i)}>Open session</button>{/if}
          {#if !i.read}<button class="tiny" onclick={() => onRead(i)}>Mark read</button>{/if}
        </div>
      </article>
    {/each}
  </div>
</aside>

<style>
  .inbox { width: 340px; flex: 0 0 340px; display: flex; flex-direction: column; background: var(--mantle); border-left: 1px solid var(--s0); min-height: 0; }
  .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--s0); }
  .badge { background: var(--mauve); color: var(--crust); border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 600; }
  .grow { flex: 1; }
  .list { flex: 1; overflow: auto; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
  .empty { padding: 8px; }
  .item { background: var(--base); border: 1px solid var(--s0); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
  .item.unread { background: var(--s0); border-color: var(--s2); }
  .meta { display: flex; gap: 6px; align-items: center; font-size: 12px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--o1); flex: 0 0 auto; }
  .dot.agent { background: none; border: 2px solid var(--peach); box-sizing: border-box; }
  .title { font-weight: 600; font-size: 13px; }
  .body { font-size: 12px; color: var(--sub1); max-height: 220px; overflow: hidden; }
  .body :global(pre) { white-space: pre-wrap; font-size: 11px; background: var(--mantle); padding: 6px 8px; border-radius: 6px; margin: 4px 0; }
  .body :global(p) { margin: 0 0 4px; }
  .actions { display: flex; gap: 6px; margin-top: 4px; }
  .tiny { background: none; border: 1px solid var(--s1); color: var(--sub0); border-radius: 6px; padding: 2px 8px; font-size: 12px; cursor: pointer; }
  .muted { color: var(--o2); } .small { font-size: 11px; }
</style>
