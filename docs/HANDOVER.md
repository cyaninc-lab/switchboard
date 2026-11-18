# Switchboard handover (2026-11-18)

Written by the agent that built 0.1.0–0.1.8, for whoever picks it up. How to build, test and ship: `../CLAUDE.md`.

## What it is
A self-hosted manager for Claude Code sessions, for a general audience (don't tailor it to the author's own hardware).
- **Desktop app** (Windows + Linux): session tree on the left (the app's own curated list; "+ Add" pulls in existing sessions from a machine or starts a new one in a folder), terminal-like chat in the middle with a real-terminal toggle, inbox on the right. Catppuccin Mocha.
- **Daemon per machine** (Claude Agent SDK). Runs inside the app by default; optionally a background service (systemd user unit on Linux) so agents survive the app closing. A machine with no app can run the daemon alone.
- **Watches** (schedule, command, HTTP, GitHub via `gh`) either post to the inbox or wake a session that then messages you first.
- Sessions get the same power as Claude Code on that machine (permission modes incl. auto/bypass, AskUserQuestion, plan approval, "allow for this session"); limits are the user's to set.

## State: 0.1.11, in daily use
Live on the author's Linux PC and Windows work PC, each with its own daemon, connected to each other over Tailscale. Self-update verified for real (0.1.0 → 0.1.7 byte-identical to the signed release).

Done: pairing (one-time code, tokens hashed), multi-machine tree, live chat with streaming, thinking, subagents (running ones in the tree; message one through its parent via SendMessage), images (paste/drop), slash commands, model/effort/mode/account per session, multiple Claude accounts (`switchboard-daemon account add`), diffs/to-dos/context-and-cost meter, real terminal (pty/ConPTY), watches + inbox + desktop notifications, report-a-bug (scrubbed logs → pre-filled GitHub issue, no credentials in the app), self-update, Windows port.

## Decisions already made (don't re-ask)
- Monorepo `cyaninc-lab/switchboard` (private); releases in public `cyaninc-lab/switchboard-releases`. Licence undecided: keep dependencies AGPL-compatible.
- Subscription auth (`CLAUDE_CODE_OAUTH_TOKEN` per account); both of the author's accounts are personal plans.
- Transport: Tailscale/private network for now, but keep it pluggable (no TLS yet: never expose a daemon publicly).
- Multiple clients on one session are all live; the first answer to a prompt wins.
- Phone app (phase 5): Tauri mobile, Android first, push via UnifiedPush/ntfy. Keep the daemon HTTP/WS API (`docs/protocol.md`) as the only contract so a native app stays possible.
- Bug reports go to GitHub issues by copy-paste + pre-filled URL; a GitHub App for one-click is a later idea.

## Backlog, roughly in order
1. **Claude Code gaps:** `@` file mentions; rewind / edit-and-resend; copy buttons on messages and code; search within a session; loading older history beyond the latest page; background commands (Bash `run_in_background`) shown and stoppable.
2. **Background service on Windows** (and macOS later); currently Linux only.
3. **Daemon `update` command** for machines running the daemon without the app.
4. **Tokens in the OS keychain** instead of the SQLite file.
5. **Phone app** (phase 5) and push.
6. **TLS / non-Tailscale transport.**
7. Code-sign the Windows installer (SmartScreen warns today).

### From daily use (the user's list, 2026-11-18)
Grouped; **(S)** small, **(M)** medium, **(L)** large. Do as separate PRs; batch into one release.
- **Input:** multiline box, Shift+Enter for a new line **(S)**; Ctrl+Enter = "send now", as in Claude Code (its `chat:sendNow`, hint "interrupt and send"; checked in 2.1.327) **(S)**: queue the message, then interrupt; the daemon sends the queue when the interrupted turn ends; queued messages stay at the bottom **(S/M)**: today a message sent while Claude works is added to the chat at once, and the still-streaming reply pushes it up, so it reads as if sent earlier. Like Claude Code, keep queued messages pinned above the box (with the "1 queued" note) and move each into the chat when its turn starts (the client has to hold them; the daemon only sends a count).
- **Tree & projects** (a project is today just the entry's `group` string, sorted by name):
  - rename an agent or project from the right-click menu (the menu exists since 0.1.9) **(S)**;
  - order agents within a project by last activity (your message or Claude's reply), with a setting to choose later **(S/M)**: pinned stay on top, and hold the order still while the pointer is over the tree so nothing jumps under a click;
  - drag an agent to another project **(M)**; drag projects to reorder them (needs a stored project order) **(M)**;
  - archive: hide from the tree but keep name/project/settings, restorable from a list **(M)**. Simpler: make Remove itself keep them, with a "Removed" list to restore from (one feature, not two).
- **Adding:** pick several sessions at once **(S)**; a dropdown of existing projects, plus "New project" **(S)**.
- **Chat display:**
  - your typed messages vs Claude's replies are hard to tell apart when scrolling back: make them clearly different **(S/M, show a couple of looks first)**;
  - messages from another session (`message_session`, 0.1.9) arrive as ordinary user prompts and look like your own typing: show them as their own kind, e.g. "✉ from Alpha", in both the receiver's chat and as a clear step in the sender's **(S)**. The daemon already knows (the turn's `origin.from`); send it with the message;
  - file paths in messages open the file/folder **(S/M)**: Claude writes those paths, so only open existing files/folders (reveal in the file manager or a viewer), never run anything; line numbers on diffs: doable, Claude Code's tool result carries a structured patch with line numbers **(M)**.
- **Inbox:** delete an item **(S)** (removes it for every device: the inbox is per machine, shared).
- **Layout & look:** collapsible left sidebar, automatic when the window is narrow **(S/M)**; light mode (Catppuccin Latte; colours are already variables) **(M)**; font picker **(S)**: a short fixed list plus size (a webview can't easily list installed fonts). A collapsed sidebar still shows the sun when a session needs you.
- **Windows:** pop a session out into its own window **(L)**; tabs **(L)**. Probably one or the other: they solve the same problem.
- **Settings:** machine defaults for new sessions (mode, model, effort, account), and a session shows its real values instead of "default" **(M)**. Changing a default affects new sessions only.
- **Idle close as a setting:** 0.1.9 removed auto-closing (closing lost "allow for this session" and in-session timers); bring it back only as an opt-in, off by default, saying what closing forgets **(S)**.
- ~~Collapse projects / subagents~~: done in 0.1.9.

## Traps learned the hard way
- A `claude` started from inside a Claude session inherits `CLAUDE*` env vars and misbehaves; scrub them (the daemon does).
- PyInstaller one-file sidecar: the real process is a child of the bootloader. The app passes `--exit-with <pid>` and kills the process group (`kill -- -pgid`; Windows `taskkill /T`). On Windows build with `--collect-all winpty`.
- `pkill -f <pattern>` can match your own shell; use `pkill -x` or a `[x]` bracket pattern.
- The AppImage launcher detaches, so WebDriver can't follow it through an update restart; `e2e-real/update.mjs` is best run by hand with a human clicking.
- The Agent SDK's `effort` is start-time only: changing it restarts the session (resume keeps history).
- Subagent metadata comes from Claude Code's sidecar files (`projects/<p>/<session>/subagents/**/agent-*.meta.json`: `finished`, `stoppedByUser`, `parentAgentId`). That's an undocumented on-disk format; re-check it after Claude Code updates.
- Release workflow: decide on `gh api`'s exit status, not its body (a 404 body was once used as a release id). The publish step refuses to publish without latest.json, an AppImage and a setup.exe.
- Windows tests run over `ssh work` (Tailscale): a scheduled task `SwitchboardDriver` runs msedgedriver in the user's desktop session; the scratch rig is in `C:\Users\Developer\switchboard-test` (can be deleted; the installed app doesn't use it). Don't install anything system-wide there without asking.
