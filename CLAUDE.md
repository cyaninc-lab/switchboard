# Switchboard: working in this repo

Start with `docs/HANDOVER.md` (state, decisions, backlog, traps). API contract: `docs/protocol.md`.

## Layout
- `daemon/`: Python 3.12 package `switchboard_daemon` (aiohttp + SQLite + Claude Agent SDK). One per machine; owns that machine's sessions.
- `desktop/`: Tauri 2 + Svelte 5 (runes) app. `src/routes/+page.svelte` is the main UI; `src/lib/api.ts` the daemon client; `src-tauri/src/lib.rs` starts the daemon as a sidecar.
- `tools/stamp-version.py`: copies `VERSION` into package.json, Cargo.toml, tauri.conf.json, the daemon.
- `spike/`: phase 0 experiments, kept for reference (`RESULTS.md`).

## Run
- Dev venv: `~/Projects/switchboard/.venv` (`pip install -e "./daemon[test]"`).
- Daemon: `.venv/bin/switchboard-daemon serve` (127.0.0.1:7878). `--demo` serves scripted fake sessions (no Claude), see `daemon/switchboard_daemon/demo.py` for its trigger words.
- Phone app (Android): `cd desktop && npx tauri android build --debug --apk --target aarch64` (needs `ANDROID_HOME=~/android-sdk`, `NDK_HOME=$ANDROID_HOME/ndk/<ver>`, `JAVA_HOME`); install with `adb install -r src-tauri/gen/android/app/build/outputs/apk/universal/debug/app-universal-debug.apk`. Release builds (`--apk` without `--debug`) are signed with `~/.config/switchboard-android/release.jks`; the phone runs those, so a debug build won't install over it without uninstalling. The user's phone is reachable with `adb connect 100.108.176.78:5555` (Tailscale). `src-tauri/src/desktop.rs` is desktop-only; the phone app connects to daemons, it runs none.
- Desktop dev: `cd desktop && npm run tauri dev`. Release build: `npm run tauri build` (needs the sidecar: `daemon/build-sidecar.sh` first).
- Strip `CLAUDE*` env vars when launching anything that runs Claude from inside a Claude session (`env -u CLAUDECODE ...`); the daemon scrubs them itself.

## Test (all must pass before a PR)
- `.venv/bin/pytest -q daemon/tests`
- `cd desktop && npm run check && npx playwright test` (headless; demo daemon + `vite build && vite preview`, never the dev server: its reloads cause flakes).
- Real window, real Claude, for anything user-visible: `node desktop/e2e-real/<x>.mjs` drives the release build through tauri-driver/WebKitWebDriver. **The user likes to watch these: open a real window, never drive the user's own mouse/keyboard.** WebKit `getText()` is '' for ellipsis-truncated elements; read `textContent`.
- Check behaviour that depends on Claude/SDK against real Claude (Haiku is fine) before claiming it works.

## Ship
- Branch → PR → checks green → **you may merge Switchboard PRs yourself** (squash). Never push to main. Before deleting a merged base branch, retarget any PR stacked on it (deleting it closes them).
- A merge that changes `VERSION` releases: `.github/workflows/release.yml` builds Linux (AppImage, deb) + Windows (NSIS) and publishes a release in this repo (`.sig` files go to the `signatures` branch); installed apps self-update from its `latest.json`. So bump `VERSION` (then `python3 tools/stamp-version.py`) and rewrite `NOTES.md` (user-facing, plain words) in any PR the user should get.
- No Claude attribution in commits or PRs (no Co-Authored-By / session trailers, no footer).
- Secrets: `TAURI_SIGNING_PRIVATE_KEY(_PASSWORD)` are repo secrets; the signing key's backup is in the user's Bitwarden. Never print or paste tokens.
