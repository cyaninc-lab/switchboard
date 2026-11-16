<script lang="ts">
  // The real `claude` TUI for one session, via the daemon's pseudo-terminal (xterm.js, Catppuccin Mocha).
  import { onMount } from 'svelte';
  import { Terminal } from '@xterm/xterm';
  import { FitAddon } from '@xterm/addon-fit';
  import '@xterm/xterm/css/xterm.css';
  import type { Live } from './api';

  let { live, entry, listen }: { live: Live; entry: string; listen: (fn: ((data: string) => void) | null) => void } = $props();
  let el: HTMLDivElement;

  onMount(() => {
    const term = new Terminal({
      fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 13, cursorBlink: true, allowProposedApi: true,
      theme: {
        background: '#1e1e2e', foreground: '#cdd6f4', cursor: '#f5e0dc', selectionBackground: '#585b70',
        black: '#45475a', red: '#f38ba8', green: '#a6e3a1', yellow: '#f9e2af', blue: '#89b4fa', magenta: '#f5c2e7', cyan: '#94e2d5', white: '#bac2de',
        brightBlack: '#585b70', brightRed: '#f38ba8', brightGreen: '#a6e3a1', brightYellow: '#f9e2af', brightBlue: '#89b4fa', brightMagenta: '#f5c2e7', brightCyan: '#94e2d5', brightWhite: '#a6adc8',
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    fit.fit();
    listen((data) => term.write(data));
    live.terminalOpen(entry, term.cols, term.rows);
    term.onData((d) => live.terminalInput(entry, d));
    term.onResize(({ cols, rows }) => live.terminalResize(entry, cols, rows));
    const ro = new ResizeObserver(() => fit.fit());
    ro.observe(el);
    term.focus();
    return () => { ro.disconnect(); listen(null); try { live.terminalClose(entry); } catch {} term.dispose(); };
  });
</script>

<div class="xterm-host" bind:this={el}></div>

<style>
  .xterm-host { flex: 1; min-height: 0; padding: 8px 0 0 12px; background: #1e1e2e; }
</style>
