# Switchboard handover (2026-11-18)

Written by the agent that built 0.1.0–0.1.8, for whoever picks it up. How to build, test and ship: `../CLAUDE.md`.

## What it is
A self-hosted manager for Claude Code sessions, for a general audience (don't tailor it to the author's own hardware).
- **Desktop app** (Windows + Linux): session tree on the left (the app's own curated list; "+ Add" pulls in existing sessions from a machine or starts a new one in a folder), terminal-like chat in the middle with a real-terminal toggle, inbox on the right. Catppuccin Mocha.
- **Daemon per machine** (Claude Agent SDK). Runs inside the app by default; optionally a background service (systemd user unit on Linux) so agents survive the app closing. A machine with no app can run the daemon alone.
- **Watches** (schedule, command, HTTP, GitHub via `gh`) either post to the inbox or wake a session that then messages you first.
- Sessions get the same power as Claude Code on that machine (permission modes incl. auto/bypass, AskUserQuestion, plan approval, "allow for this session"); limits are the user's to set.

## State: 0.1.8, in daily use
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

## Traps learned the hard way
- A `claude` started from inside a Claude session inherits `CLAUDE*` env vars and misbehaves; scrub them (the daemon does).
- PyInstaller one-file sidecar: the real process is a child of the bootloader. The app passes `--exit-with <pid>` and kills the process group (`kill -- -pgid`; Windows `taskkill /T`). On Windows build with `--collect-all winpty`.
- `pkill -f <pattern>` can match your own shell; use `pkill -x` or a `[x]` bracket pattern.
- The AppImage launcher detaches, so WebDriver can't follow it through an update restart; `e2e-real/update.mjs` is best run by hand with a human clicking.
- The Agent SDK's `effort` is start-time only: changing it restarts the session (resume keeps history).
- Subagent metadata comes from Claude Code's sidecar files (`projects/<p>/<session>/subagents/**/agent-*.meta.json`: `finished`, `stoppedByUser`, `parentAgentId`). That's an undocumented on-disk format; re-check it after Claude Code updates.
- Release workflow: decide on `gh api`'s exit status, not its body (a 404 body was once used as a release id). The publish step refuses to publish without latest.json, an AppImage and a setup.exe.
- Windows tests run over `ssh work` (Tailscale): a scheduled task `SwitchboardDriver` runs msedgedriver in the user's desktop session; the scratch rig is in `C:\Users\Developer\switchboard-test` (can be deleted; the installed app doesn't use it). Don't install anything system-wide there without asking.
