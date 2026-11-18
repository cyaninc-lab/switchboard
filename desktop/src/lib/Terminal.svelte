<script lang="ts">
  // The real `claude` TUI for one session, via the daemon's pseudo-terminal (xterm.js, in the app's Catppuccin theme).
  import { onMount } from 'svelte';
  import { Terminal } from '@xterm/xterm';
  import { FitAddon } from '@xterm/addon-fit';
  import '@xterm/xterm/css/xterm.css';
  import type { Live } from './api';
  import { theme, xtermTheme, fonts, MONO_FONTS } from './themes.svelte';

  let { live, entry, listen }: { live: Live; entry: string; listen: (fn: ((data: string) => void) | null) => void } = $props();
  let el: HTMLDivElement;
  let xterm: Terminal | undefined;
  $effect(() => { const t = xtermTheme(theme.name); if (xterm) xterm.options.theme = t; });  // follows a theme change while open
  $effect(() => { const f = MONO_FONTS[fonts.mono].css; if (xterm) xterm.options.fontFamily = f; });  // and a font change

  onMount(() => {
    const term = new Terminal({
      fontFamily: MONO_FONTS[fonts.mono].css, fontSize: 13, cursorBlink: true, allowProposedApi: true,
      theme: xtermTheme(theme.name),
    });
    xterm = term;
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
  .xterm-host { flex: 1; min-height: 0; padding: 8px 0 0 12px; background: var(--base); }
</style>
