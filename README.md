# Switchboard

Self-hosted session manager for Claude Code, powered by Claude.

- One tree of your Claude sessions across machines; stopped sessions stay listed and resume with one click.
- A small daemon per machine (Python, Claude Agent SDK) owns that machine's sessions; desktop and phone apps connect to any number of daemons.
- Any entry can become an agent: watches (GitHub, schedules, HTTP checks, webhooks, plugins) wake it and it messages you first.
- Same power and permission modes as Claude Code on that machine; Switchboard only adds the remote access.

Status: desktop app for Windows and Linux, in daily use. Install from [the releases page](https://github.com/cyaninc-lab/switchboard-releases/releases/latest); it updates itself.

Layout: `daemon/` (Python), `desktop/` (Tauri + Svelte), `mobile/` (later), `spike/` (phase 0 experiments). Working on it: `CLAUDE.md`, then `docs/HANDOVER.md`.
