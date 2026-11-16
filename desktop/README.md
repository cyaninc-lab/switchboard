# Switchboard desktop

Tauri 2 + SvelteKit (Svelte 5), Catppuccin Mocha. Talks to daemons only through `src/lib/api.ts`
(plain fetch, no Tauri APIs), so the UI can also run in a browser or a mobile build.

```
npm install
../daemon/build-sidecar.sh   # freezes the daemon into src-tauri/binaries/ (needed before dev/build)
npm run tauri dev        # app window with hot reload (needs a daemon: ../daemon)
npx vite dev             # UI only, in a browser at http://localhost:1420
npm run check            # type check
npx tauri build          # release build + installers
```

Linux build deps: `libwebkit2gtk-4.1-dev build-essential libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev`.

The app carries the daemon as a sidecar: on start it uses a daemon already running on this machine
(the optional background service) or starts its own, pairs with it automatically, and the daemon exits
when the app does (`--exit-with <app pid>`, so a crash cleans up too).

Phase 1: connect machines (one-time code), the tree (add from a machine's sessions, pin, remove),
read-only history. Tokens sit in localStorage for now; they move to the OS keychain before release.

## Testing without a screen

`npm run test:e2e` runs the real UI in headless Chromium against a daemon in demo mode
(`switchboard-daemon serve --demo`: canned sessions, no Claude usage, nothing real touched).
Screenshots land in `e2e/.results/` (CI uploads them as the `ui-screenshots` artifact).
Later, the same flows can drive the actual Tauri window through `tauri-driver` on a virtual display (Xvfb).
