// Catppuccin themes (https://catppuccin.com/palette): the app's colour variables come from the chosen palette.
// The chosen theme is remembered on this device.

type Palette = Record<'rosewater' | 'pink' | 'mauve' | 'red' | 'peach' | 'yellow' | 'green' | 'teal' | 'sky' | 'blue' | 'text' | 'subtext1'
  | 'subtext0' | 'overlay2' | 'overlay1' | 'overlay0' | 'surface2' | 'surface1' | 'surface0' | 'base' | 'mantle' | 'crust', string>;

export const THEMES: Record<string, { label: string; dark: boolean; p: Palette }> = {
  latte: { label: 'Latte (light)', dark: false, p: {
    rosewater: '#dc8a78', pink: '#ea76cb', mauve: '#8839ef', red: '#d20f39', peach: '#fe640b', yellow: '#df8e1d', green: '#40a02b',
    teal: '#179299', sky: '#04a5e5', blue: '#1e66f5', text: '#4c4f69', subtext1: '#5c5f77', subtext0: '#6c6f85', overlay2: '#7c7f93',
    overlay1: '#8c8fa1', overlay0: '#9ca0b0', surface2: '#acb0be', surface1: '#bcc0cc', surface0: '#ccd0da', base: '#eff1f5', mantle: '#e6e9ef', crust: '#dce0e8' } },
  frappe: { label: 'Frappé', dark: true, p: {
    rosewater: '#f2d5cf', pink: '#f4b8e4', mauve: '#ca9ee6', red: '#e78284', peach: '#ef9f76', yellow: '#e5c890', green: '#a6d189',
    teal: '#81c8be', sky: '#99d1db', blue: '#8caaee', text: '#c6d0f5', subtext1: '#b5bfe2', subtext0: '#a5adce', overlay2: '#949cbb',
    overlay1: '#838ba7', overlay0: '#737994', surface2: '#626880', surface1: '#51576d', surface0: '#414559', base: '#303446', mantle: '#292c3c', crust: '#232634' } },
  macchiato: { label: 'Macchiato', dark: true, p: {
    rosewater: '#f4dbd6', pink: '#f5bde6', mauve: '#c6a0f6', red: '#ed8796', peach: '#f5a97f', yellow: '#eed49f', green: '#a6da95',
    teal: '#8bd5ca', sky: '#91d7e3', blue: '#8aadf4', text: '#cad3f5', subtext1: '#b8c0e0', subtext0: '#a5adcb', overlay2: '#939ab7',
    overlay1: '#8087a2', overlay0: '#6e738d', surface2: '#5b6078', surface1: '#494d64', surface0: '#363a4f', base: '#24273a', mantle: '#1e2030', crust: '#181926' } },
  mocha: { label: 'Mocha', dark: true, p: {
    rosewater: '#f5e0dc', pink: '#f5c2e7', mauve: '#cba6f7', red: '#f38ba8', peach: '#fab387', yellow: '#f9e2af', green: '#a6e3a1',
    teal: '#94e2d5', sky: '#89dceb', blue: '#89b4fa', text: '#cdd6f4', subtext1: '#bac2de', subtext0: '#a6adc8', overlay2: '#9399b2',
    overlay1: '#7f849c', overlay0: '#6c7086', surface2: '#585b70', surface1: '#45475a', surface0: '#313244', base: '#1e1e2e', mantle: '#181825', crust: '#11111b' } },
};

const KEY = 'switchboard.theme';
const saved = (() => { try { return localStorage.getItem(KEY); } catch { return null; } })();
export const theme = $state({ name: saved && saved in THEMES ? saved : 'mocha' });

/** The app's CSS variables (short names used in the stylesheet) for a palette. */
function vars(p: Palette): Record<string, string> {
  return { crust: p.crust, mantle: p.mantle, base: p.base, s0: p.surface0, s1: p.surface1, s2: p.surface2, o0: p.overlay0, o1: p.overlay1,
    o2: p.overlay2, sub0: p.subtext0, sub1: p.subtext1, text: p.text, mauve: p.mauve, green: p.green, peach: p.peach, sky: p.sky,
    teal: p.teal, red: p.red, yellow: p.yellow, blue: p.blue, pink: p.pink, rosewater: p.rosewater };
}

export function applyTheme(name: string) {
  const t = THEMES[name] ?? THEMES.mocha;
  theme.name = name in THEMES ? name : 'mocha';
  try { localStorage.setItem(KEY, theme.name); } catch {}
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(vars(t.p))) root.style.setProperty(`--${k}`, v);
  root.style.colorScheme = t.dark ? 'dark' : 'light';  // native controls (scrollbars, selects) follow
  root.dataset.theme = theme.name;
}

/** xterm.js colours for the real terminal. */
export function xtermTheme(name: string) {
  const p = (THEMES[name] ?? THEMES.mocha).p;
  return {
    background: p.base, foreground: p.text, cursor: p.rosewater, selectionBackground: p.surface2,
    black: p.surface1, red: p.red, green: p.green, yellow: p.yellow, blue: p.blue, magenta: p.pink, cyan: p.teal, white: p.subtext1,
    brightBlack: p.surface2, brightRed: p.red, brightGreen: p.green, brightYellow: p.yellow, brightBlue: p.blue, brightMagenta: p.pink,
    brightCyan: p.teal, brightWhite: p.subtext0,
  };
}
