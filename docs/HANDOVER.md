# Switchboard handover (2026-11-18)

Written by the agent that built 0.1.0–0.1.8, for whoever picks it up. How to build, test and ship: `../CLAUDE.md`.

## What it is
A self-hosted manager for Claude Code sessions, for a general audience (don't tailor it to the author's own hardware).
- **Desktop app** (Windows + Linux): session tree on the left (the app's own curated list; "+ Add" pulls in existing sessions from a machine or starts a new one in a folder), terminal-like chat in the middle with a real-terminal toggle, inbox on the right. Catppuccin Mocha.
- **Daemon per machine** (Claude Agent SDK). Runs inside the app by default; optionally a background service (systemd user unit on Linux) so agents survive the app closing. A machine with no app can run the daemon alone.
- **Watches** (schedule, command, HTTP, GitHub via `gh`) either post to the inbox or wake a session that then messages you first.
- Sessions get the same power as Claude Code on that machine (permission modes incl. auto/bypass, AskUserQuestion, plan approval, "allow for this session"); limits are the user's to set.

## State: 0.1.18, in daily use
Live on the author's Linux PC and Windows work PC, each with its own daemon, connected to each other over Tailscale. Self-update verified for real (0.1.0 → 0.1.7 byte-identical to the signed release).

Done: pairing (one-time code, tokens hashed), multi-machine tree, live chat with streaming, thinking, subagents (running ones in the tree; message one through its parent via SendMessage), images (paste/drop), slash commands, model/effort/mode/account per session, multiple Claude accounts (`switchboard-daemon account add`), diffs/to-dos/context-and-cost meter, real terminal (pty/ConPTY), watches + inbox + desktop notifications, report-a-bug (scrubbed logs → pre-filled GitHub issue, no credentials in the app), self-update, Windows port.

## Decisions already made (don't re-ask)
- Monorepo `cyaninc-lab/switchboard`, public since 2026-11-18 under **AGPL-3.0** (dependencies must stay AGPL-compatible); releases in this repo since 0.1.15 (before: `cyaninc-lab/switchboard-releases`, archived 2026-11-19 once every install was on 0.1.15+); `.sig` files go to the `signatures` branch (`tools/move-signatures.sh`, run by the release workflow), since `latest.json` already carries the signatures. The private history before that, with its PRs (#1–#58 there), is archived in `cyaninc-lab/switchboard-archive`. Commits use the no-reply address `59282225+MalachiAzrael@users.noreply.github.com`, never a personal one.
- Subscription auth (`CLAUDE_CODE_OAUTH_TOKEN` per account); both of the author's accounts are personal plans.
- Transport: Tailscale/private network for now, but keep it pluggable (no TLS yet: never expose a daemon publicly).
- Multiple clients on one session are all live; the first answer to a prompt wins.
- Phone app (phase 5): Tauri mobile, Android first, push via UnifiedPush/ntfy. Keep the daemon HTTP/WS API (`docs/protocol.md`) as the only contract so a native app stays possible.
- Bug reports: "Send to GitHub" files the issue through the "Switchboard reports" GitHub App (client id in `daemon/switchboard_daemon/github.py`; device flow, no secret, Issues read/write on this repo only, installed on cyaninc-lab/switchboard; user tokens don't expire). The daemon does the GitHub calls (GitHub's sign-in has no CORS) and keeps the token. "Open on GitHub instead" is the old copy + pre-filled URL, no sign-in.

## Backlog, roughly in order
1. **Claude Code gaps:** rewind / edit-and-resend; search within a session; loading older history beyond the latest page.
2. **Background service on Windows** (and macOS later); currently Linux only.
3. **Daemon `update` command** for machines running the daemon without the app.
4. **Tokens in the OS keychain** instead of the SQLite file.
5. **Phone app** (phase 5) and push: planned, see "Phone app plan" below.
6. **TLS / non-Tailscale transport.**
7. Code-sign the Windows installer (SmartScreen warns today).

### From daily use (the user's list, 2026-11-18)
Shipped in 0.1.9–0.1.14: multiline input, Ctrl+Enter send-now, queued messages at the bottom, rename from the right-click menu, project dropdown + "New project" when adding, add several at once, inbox delete, Catppuccin themes, text size and fonts, boxes for messages between sessions, a band for your own messages, clickable file links, web links in the default browser, collapsible projects. In 0.1.16–0.1.18: collapsible session tree, idle close as a per-machine setting, a tag on a machine's chip when it runs an older/newer Switchboard, tabs, pop-out windows, option previews in questions. In 0.1.19: thin scroll bars, activity order, drag projects, drag a tab out, `@` mentions, copy buttons, remove/delete, three fonts. After 0.1.19 (unreleased): /clear follows the new session, background tasks bar, Send to GitHub, watch active hours + quiet + "Ask Claude to check something" (for e.g. Zoho Desk mentions through the user's Claude Zoho integration; Zoho webhooks need a Professional plan and a public endpoint, so not used).

Left, **(S)** small, **(M)** medium, **(L)** large. Separate PRs, batched into a release when the user says so:

## Phone app plan (agreed with the user, 2026-11-20; not started)
For "Claude needs me while I'm away from my desk", with a real chat from the start. Android first, Tauri 2 mobile, the same Svelte code laid out for a phone; the daemon's HTTP/WS API stays the only contract.
- **Screens:** Sessions (every machine, same icons, the ones that need you on top with what they're asking; tabs Sessions / Inbox / Settings) · Chat · Pairing.
- **Chat like text messages:** the user on the right (mauve bubbles), Claude on the left (grey); tool steps, to-dos and diffs as slim outlined cards on Claude's side (tap to expand). Message box with Send (queues while Claude works; hold Send = send now/interrupt), photos from camera/gallery, keyboard dictation, slash and `@` menus, Stop in the header. Permission/question/plan prompts slide up as a sheet with big buttons. Starting a new session in a recent folder is in.
- **Notifications:** pushed by each daemon to the user's own ntfy (on the Pi), delivered to the app through UnifiedPush (no Google). Actions: Allow / Deny / Open on permission prompts, an inline reply on questions and finished turns. Notifications carry no secrets; the app fetches details over Tailscale when you act. The lock screen follows Android's own "hide sensitive content" setting (each notification carries a short public version: "The RIG GH needs you: permission needed"); a switch of our own was tried and dropped, Android ignores public versions when it shows all content. Settings: **tell me when a session finishes** (on by default); watches notify as on the desktop.
- **Reaching machines:** over Tailscale, like the desktop. Pairing by QR code: desktop Settings → "Pair a phone" shows address + one-time code. Tokens in Android's Keystore.
- **Updates:** a signed APK on the releases page; the app checks the feed itself (Tauri's updater doesn't do mobile).
- **Agents can call you** (agreed 2026-11-20, in the app, no phone network/Twilio): sessions get a `call_user(reason)` tool (next to `message_session`). The phone rings with a full-screen incoming call (Android call-style notification via the same ntfy push, urgent). Answer: the phone reads Claude's message aloud (on-device text to speech) and listens; your speech becomes text (on-device speech recognition where available) and goes to the session as a message; replies are read out, back and forth until you hang up; the call is also kept in the chat as text. Decline/missed: a notification + inbox item, reply in text. Guardrails: quiet hours (rings become silent notifications), a per-session "may call me" switch, at most one call every few minutes; the desktop app rings too when open. Turn-based (Claude thinks between turns), not a live voice line.
- **Not in the first version:** the real terminal, watches setup, accounts, split view, tabs, pop-out.
- **Build order:** (1) phone layout + the chat screen in the existing UI, usable in a phone browser over Tailscale (the daemon serves the web app at its address for this); (2) Android shell: QR pairing, Keystore, sessions; (3) ntfy push with Allow/Deny and inline reply; (4) signed APK releases + in-app update check; (5) calls.
- Mockup (screens 1–4) was shown to the user; regenerate from this description if needed.
- **Done:** step 1a (#48, the daemon serves the web app) and 1b (the phone layout: narrow touch screens only, `(max-width: 640px) and (pointer: coarse)`; one screen at a time, chat as text messages, Send button, bottom bar). Step 2 (#60, the Android app, QR pairing). Step 3 (notifications): the user's ntfy runs on yagoo (`/mnt/storage/compose/ntfy`, port 2586, not GitOps, no login: the topic `sb-<32 hex>` is the only lock). The app subscribes itself (Settings → Phone notifications; the user never sees ntfy); `PushService.kt` is a foreground service streaming the topic (no Google/UnifiedPush), Allow/Deny and Reply go straight to the daemon over HTTP with the token in the app's `push.json`. Next: step 4.
- **Backlog after the phone app:** drop the Tailscale requirement (options: a Caddy reverse proxy with TLS, a relay, or TLS in the daemon).

## Traps learned the hard way
- The split pane is the single-session page (`?solo=&embed=1`) in an iframe. Tauri doesn't give iframes `__TAURI_INTERNALS__`, so the pane asks the main window over postMessage to open links/files and to notify (it announces `pane-ready`, the main window answers `sb-shell`; the main window only accepts requests from its own pane). Svelte state proxies can't be posted: send `$state.snapshot(...)`.
- Real-window tests: when no Switchboard runs on this machine, the test app starts its own daemon on 7878, which uses the real Claude login. `e2e-real/windows.mjs` sets `SWITCHBOARD_CLAUDE=/bin/false` and picks the demo machine explicitly; copy that.
- The text size zooms the root element. Under zoom, WebView2 does NOT scale `vh` but WebKitGTK does, so `100vh` (or any calc on it) is wrong on one of them: size full-window things with `position: fixed; inset: 0`. Check zoom-related layout in both engines (Playwright Chromium for WebView2, the real Linux app for WebKitGTK).
- HTML drag and drop (tabs, projects, dropping images) needs Tauri's `dragDropEnabled: false` on every window, or Windows' WebView2 never delivers the drag to the page. Set in tauri.conf.json and in shell.ts `popOut`.
- Pop-out windows: a session is open in one place only (a tab or its own window). The window is the same page with `?machine=&solo=`; the draft goes over in localStorage (`switchboard.handoff.<id>`) and comes back in a `solo-closed` event to the main window, which owns tree, inbox, unread dots and notifications. Tauri's `onCloseRequested` ends by destroying the window, so `core:window:allow-destroy` is needed or the window never closes. Closing the main window exits the app (lib.rs `on_window_event`).
- Real-window tests share the installed app's data (same identifier) and its daemon on 7878: `e2e-real/windows.mjs` isolates itself with `XDG_*` dirs and a demo daemon on 7899; copy that.
- A new repo whose automatic Actions runs never start (push/PR do nothing, a manual `workflow_dispatch` works): GitHub's Actions setup for it got stuck at creation. Turning Actions off and on fixes it: `gh api -X PUT repos/OWNER/REPO/actions/permissions -F enabled=false`, then `-F enabled=true -f allowed_actions=all`. Seen 2026-11-18 when the repo went public.
- Tests must not depend on github.com: the Report a bug test stubs it (a public repo sends logged-out visitors of `/issues/new` to the login page).
- Merging a stacked PR with `--delete-branch` closes the PR stacked on it: retarget the next one to `main` first (and rebase it onto `main` with `git rebase --onto origin/main <old base> <branch>`).
- A shell script with `set -e` does not stop on a failing `a && b` list: check results explicitly.
- A `claude` started from inside a Claude session inherits `CLAUDE*` env vars and misbehaves; scrub them (the daemon does).
- PyInstaller one-file sidecar: the real process is a child of the bootloader. The app passes `--exit-with <pid>` and kills the process group (`kill -- -pgid`; Windows `taskkill /T`). On Windows build with `--collect-all winpty`.
- `pkill -f <pattern>` can match your own shell; use `pkill -x` or a `[x]` bracket pattern.
- The AppImage launcher detaches, so WebDriver can't follow it through an update restart; `e2e-real/update.mjs` is best run by hand with a human clicking.
- The Agent SDK's `effort` is start-time only: changing it restarts the session (resume keeps history).
- Subagent metadata comes from Claude Code's sidecar files (`projects/<p>/<session>/subagents/**/agent-*.meta.json`: `finished`, `stoppedByUser`, `parentAgentId`). That's an undocumented on-disk format; re-check it after Claude Code updates.
- Release workflow: decide on `gh api`'s exit status, not its body (a 404 body was once used as a release id). The publish step refuses to publish without latest.json, an AppImage and a setup.exe.
- Windows tests run over `ssh work` (Tailscale): a scheduled task `SwitchboardDriver` runs msedgedriver in the user's desktop session; the scratch rig is in `C:\Users\Developer\switchboard-test` (can be deleted; the installed app doesn't use it). Don't install anything system-wide there without asking.
