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
- Bug reports go to GitHub issues by copy-paste + pre-filled URL; a GitHub App for one-click is a later idea.

## Backlog, roughly in order
1. **Claude Code gaps:** rewind / edit-and-resend; search within a session; loading older history beyond the latest page; background commands (Bash `run_in_background`) shown and stoppable.
2. **Background service on Windows** (and macOS later); currently Linux only.
3. **Daemon `update` command** for machines running the daemon without the app.
4. **Tokens in the OS keychain** instead of the SQLite file.
5. **Phone app** (phase 5) and push.
6. **TLS / non-Tailscale transport.**
7. Code-sign the Windows installer (SmartScreen warns today).

### From daily use (the user's list, 2026-11-18)
Shipped in 0.1.9–0.1.14: multiline input, Ctrl+Enter send-now, queued messages at the bottom, rename from the right-click menu, project dropdown + "New project" when adding, add several at once, inbox delete, Catppuccin themes, text size and fonts, boxes for messages between sessions, a band for your own messages, clickable file links, web links in the default browser, collapsible projects. In 0.1.16–0.1.18: collapsible session tree, idle close as a per-machine setting, a tag on a machine's chip when it runs an older/newer Switchboard, tabs, pop-out windows, option previews in questions. Next up (the user's pick, 2026-11-19): thin scroll bars, order by last activity within a project, drag projects to reorder, drag a tab out to pop it out, `@` file mentions, copy buttons.

Left, **(S)** small, **(M)** medium, **(L)** large. Separate PRs, batched into a release when the user says so:
- **Line numbers on diffs** **(M)**: probably from Claude Code's structured patch in the tool result; not checked.
- **Drag an agent to another project** **(M)**.
- **Archive**, or better: make Remove keep name/project/settings, with a "Removed" list to restore from **(M)**.
- **Settings defaults** for new sessions (mode, model, effort, account), and sessions show real values instead of "default" **(M)**; a changed default affects new sessions only.
- **Split view** **(M)**: two tabs side by side in one window, now that tabs exist.
- **`/btw` side chat** **(M)**: `/btw` opens a chat panel on the right (where the inbox is) for side questions about the session: like Claude Code's `/btw`, it sees the session's context but doesn't go into its history or interrupt it. Kept per session like drafts (only the current session's shows; × clears it; not saved across restarts); an answer arriving while you're elsewhere gives the session its unread dot.

## Traps learned the hard way
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
