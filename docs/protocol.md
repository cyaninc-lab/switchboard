# Daemon protocol (draft v1)

The daemon's HTTP + WebSocket API is the contract every client is built against: the Tauri
desktop app, the Tauri mobile app, and any later native app. No client logic lives in
Tauri-specific code. Everything is under `/v1`; breaking changes get `/v2`.

## Auth and pairing

- The daemon listens on `127.0.0.1` until an address is added to its config. TLS on every
  non-localhost address (self-signed by default; the client pins the fingerprint at pairing).
- `switchboard-daemon pair` prints a one-time code (8 characters, 10 minutes, single use).
- `POST /v1/pair` `{code, client_name}` → `{token, daemon_id, fingerprint}`. Tokens are long-lived,
  stored hashed, listed and revoked with `switchboard-daemon clients`.
- Every other request: `Authorization: Bearer <token>`.

## HTTP

| Method + path | Does |
| --- | --- |
| `GET /v1/machine` | name, daemon + SDK versions, accounts (labels only), default account and mode |
| `GET /v1/sessions?q=&limit=&offset=` | this machine's sessions from `list_sessions()`, each with `entry_id` if it's in the tree |
| `GET /v1/entries` | tree entries stored here: id, session_id, name, group, pinned, account, mode, state, watch ids |
| `POST /v1/entries` | add: `{session_id}` (existing) or `{new: {cwd}}` (a new session: the daemon picks its id up front and starts it with that id on the first message or terminal), plus name, group, account?, mode? |
| `GET /v1/accounts` · `POST /v1/accounts` · `DELETE /v1/accounts/{name}` | extra Claude logins on this machine (`{name, token, kind: oauth|api_key}`); tokens are stored by the daemon and never returned |
| `GET /v1/logs?lines=` | the daemon's recent log, secrets scrubbed (for bug reports) |
| `GET /v1/models` | the models this machine's Claude Code offers (value, displayName, description, effort levels) |
| `GET /v1/folders` | folder suggestions for a new session (where sessions have run, newest first) |
| `PATCH /v1/entries/{id}` | rename, regroup, pin, change account or default mode |
| `GET /v1/entries/removed` · `POST /v1/entries/{id}/restore` | the Removed list (entries taken off the tree, kept with name, project and settings; newest first, with `removed` ms) and putting one back as it was. Adding a removed session again with `POST /v1/entries` restores it too (200), with that request's name and project |
| `DELETE /v1/entries/{id}` | off the tree (into the Removed list); the session stays on disk. `?forever=1` also deletes the session from the machine (`delete_session`: transcript + subagent transcripts); 409 `open_elsewhere` while a terminal elsewhere holds it |
| `GET /v1/entries/{id}/files?q=` | files in the session's folder for @-mentions, best matches first (git's ignore rules honoured; node_modules and the like skipped) |
| `GET /v1/entries/{id}/messages?offset=&limit=` | history via `get_session_messages()`, no resume needed |
| `GET /v1/commands` | slash commands for the menu, with descriptions where known |
| `GET/POST /v1/watches` · `PATCH/DELETE /v1/watches/{id}` | watches: `{name, kind: schedule|command|http|github, config, action: notify|wake, entry_id?, instructions?, mode?, enabled}`; command watches run shell commands on the daemon's machine (same trust as a paired client) |
| `POST /v1/watches/{id}/test` | run the check now and fire even if nothing changed |
| `GET/DELETE /v1/github` · `POST /v1/github/device` · `POST /v1/github/poll` · `POST /v1/github/issue` | bug reports to GitHub, from this machine only: sign-in status / sign out; start a device sign-in (the "Switchboard reports" GitHub App: Issues on the Switchboard repo only); poll it (`pending`, `slow_down`, `done` + login, `expired`, `denied`); file `{title, body}` as an issue (401 `not_signed_in`) |
| `GET /v1/settings` · `PUT /v1/settings` | daemon-wide settings: `idle_close_minutes` (0 = off, the default, or 10, 30, 60, 240); `project_order` (project names, top first; unlisted ones follow). `new_session` (`{mode, model, effort, account}` that sessions added from then on start with; empty = Claude Code's own) and, read-only, `claude_default_mode` (Claude Code's `permissions.defaultMode` on that machine). A change to `project_order` broadcasts `{type: settings}` |
| `GET /v1/inbox` · `POST /v1/inbox/read` · `POST /v1/inbox/{id}/read` · `DELETE /v1/inbox/{id}` | what watches and woken agents have to say (last 500 kept) |

## WebSocket `/v1/ws`

One socket per client per daemon. JSON messages with a `type`. The first client message must be
`{"type": "auth", "token": ...}` (browsers can't set headers on a WebSocket); the daemon answers `hello`.

Client → daemon:

| type | fields |
| --- | --- |
| `subscribe` | `entries: [id]` (or `"all"`) |
| `send` | `entry, text, images?, now?` (starts or resumes the session if stopped; `now`: if it's working, interrupt and send this first, Claude Code's Ctrl+Enter; images = `[{media_type, data(base64)}]`, PNG/JPEG/GIF/WebP, ≤5 MB each, ≤10) |
| `interrupt` / `stop` | `entry` |
| `set_mode` | `entry, mode` (default, acceptEdits, plan, auto, dontAsk, bypassPermissions) |
| `set_account` | `entry, account` (null = the machine's own login; closes a live session, which resumes on the new account at the next message) |
| `set_effort` | `entry, effort` (low/medium/high/xhigh/max or null; applies when the session starts, so a live one restarts, after the running turn if busy) |
| `set_model` | `entry, model` (a value from `/v1/models`; null = Claude Code's default; switches a live session) |
| `permission_reply` | `request_id, allow, message?, remember? (session or localSettings), answers? ({question: label or [labels]}), response? (free text)` |
| `terminal_open` / `terminal_input` / `terminal_resize` / `terminal_close` | real-terminal toggle: PTY running `claude --resume` (the SDK session is closed first) |

Daemon → client:

| type | fields |
| --- | --- |
| `state` | `entry, state` (new, working, waiting, ready = live and idle, stopped, terminal, error) |
| `delta` | `entry, text, kind?` (partial streaming; `kind: thinking` for thinking) |
| `message` | `entry, message` (same shape as history; a subagent's messages carry `parent_tool_use_id` = the Agent tool call) |
| `permission_request` | `request_id, entry, tool, kind (tool, question = AskUserQuestion, plan = ExitPlanMode), input, remember (which 'remember' choices Claude Code offers)` |
| `permission_resolved` | `request_id, allow, by` (first answer wins; every client is told) |
| `inbox` / `inbox_read` / `inbox_removed` | a new item / items marked read / an item deleted (`id`) |
| `terminal_opened` / `terminal_closed` | `entry` (the TUI started / exited) |
| `terminal_output` | `entry, data` |
| `mode` / `model` / `account` | the new value (after a change) |
| `commands` | `entry, commands` (slash commands the session reported at start) |
| `notice` | `entry, text` (e.g. conversation compacted) |
| `usage` | `entry, context_used, context_window, turn_cost, session_cost` (after each turn; costs are API-equivalent) |
| `queued` | `entry, count, sent?, dropped?` (a message sent while working waits for the turn to end; `sent`: how many just left the queue to be sent, clients move them into the chat; `dropped`: interrupt cleared the queue, clients give the text back) |
| `error` | `entry?, code, message` |

Every subscribed client receives every event, so several clients stay live on one session.

## Daemon behaviour

- One `ClaudeSDKClient` per active entry. It stays open until a client sends `stop`, the daemon exits, or (only when
  `idle_close_minutes` is set) it has been quiet that long with nothing working, pending, queued or running in the background;
  a stopped entry is resumed on the next `send` or watch.
- Every session the daemon opens gets an in-process MCP server `switchboard` with two tools, allowed without asking:
  `sessions` (the other tree entries on this machine and their state) and `message_session(session, text)`
  (by entry name or id). The message is sent to the target as a prompt (announced to clients like a watch's), resuming it if stopped
  and queued if it's working. Targets open in a terminal outside the daemon are refused (Claude Code's SendMessage reaches those).
- Spawned sessions get a clean environment (no inherited `CLAUDE*` vars) plus the entry's
  account token (`CLAUDE_CODE_OAUTH_TOKEN`) when it isn't the machine default.
- Sessions load the user's own settings (`setting_sources` default), so their permission rules apply.
- A session open in a terminal elsewhere shows as `terminal` and refuses `send`.
- Background tasks (Claude Code's `task_*` system messages: Bash `run_in_background`, background agents): kept per live session (running ones plus the last 5 finished) and sent as `{type: tasks, entry, tasks: [{id, status: running|completed|failed|stopped, started, ended?, description, type, summary?, output_file?}]}`; also in each entry's `tasks`. A client stops one with `{type: stop_task, entry, task_id}` (the SDK's `stop_task`). Running ones keep idle close away; closing the session sends `tasks: []`.
- `/clear` (or any reset) makes Claude Code start a new session id: the daemon moves the entry to it and broadcasts `{type: session_reset, entry, session_id}` (clients empty the chat). History and resumes then use the new session; the old transcript stays on disk.
- Storage: one SQLite file in the daemon's data dir (entries, watches, inbox, client tokens).
