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

Outside `/v1/`, the daemon serves the web app (the same UI as the desktop app, bundled into the daemon; `SWITCHBOARD_WEBUI` points elsewhere): its files are public, with unknown paths answered by `index.html`. So a phone or another computer on the tailnet can open `http://<machine>:7878/` and pair with a code.

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
| `GET /v1/commands` | slash commands for the menu, with descriptions where known and `hint` (Claude Code's argument hint, e.g. `[low\|high] [--fix]`) |
| `GET/POST /v1/watches` · `PATCH/DELETE /v1/watches/{id}` | watches: `{name, kind: schedule|command|http|github, config, action: notify|wake, entry_id?, instructions?, mode?, enabled}`; command watches run shell commands on the daemon's machine (same trust as a paired client) |
| `POST /v1/watches/{id}/test` | run the check now and fire even if nothing changed |
| `GET/DELETE /v1/github` · `POST /v1/github/device` · `POST /v1/github/poll` · `POST /v1/github/issue` | bug reports to GitHub, from this machine only: sign-in status / sign out; start a device sign-in (the "Switchboard reports" GitHub App: Issues on the Switchboard repo only); poll it (`pending`, `slow_down`, `done` + login, `expired`, `denied`); file `{title, body}` as an issue (401 `not_signed_in`) |
| `GET /v1/settings` · `PUT /v1/settings` | daemon-wide settings: `idle_close_minutes` (0 = off, the default, or 10, 30, 60, 240); `project_order` (project names, top first; unlisted ones follow); `projects` (projects made with no sessions yet, kept until removed; a change broadcasts `{type: settings}`). `new_session` (`{mode, model, effort, account}` that sessions added from then on start with; empty = Claude Code's own) `project_defaults` (`{project: {mode, model, effort, account}}`, over `new_session` for sessions added to that project; a PUT changes only the projects it names, `{}` clears one). Read-only: `claude_default_mode` (Claude Code's `permissions.defaultMode` on that machine) and `plans` (`{account: {five_hour, seven_day: {used, resets_at}}}`, `""` = machine login, as last seen). A change to `project_order` broadcasts `{type: settings}` |
| `GET /v1/push` · `PUT /v1/push` | `{voice, listen, pause_at_desk, at_desk}`: where a phone call gets its voice and is understood, and "pause while I'm in Switchboard on a computer". Phone notifications themselves go through the account's relay (below): nothing to set here. |
| `GET /v1/relay` · `POST /v1/relay/signin {url}` · `DELETE /v1/relay` | this machine on a relay (this machine only): status, sign in with GitHub (returns the code to enter; finishes in the background, then the tunnel opens), leave. |
| `GET /v1/relay/devices` · `POST /v1/relay/devices/{id}/approve` · `DELETE /v1/relay/devices/{id}` | the account's devices, through this machine's sign-in (this machine only). |
| `POST /v1/peer_token` · `GET/PUT /v1/peers` | sessions messaging sessions on the user's other machines. Machines signed in to the same relay account find each other there with no setup (GET lists them under `on_account`; letters go through the relay with the sending machine's own credential, and the other daemon trusts it as an approved device of the account). Without an account: `POST /v1/peer_token {machine}` → `{token, machine}`: a client token named "peer: <machine>" for that other daemon (a new one replaces the old). `PUT /v1/peers {peers: [{name, url, token}]}` replaces the machines this one's sessions may message; GET lists them without tokens. The app's Settings → "Let sessions on my machines message each other" does both for every pair. The switchboard tools then list `name@machine` and deliver with `POST /v1/entries/{id}/send {text, from: "session@machine"}` |
| `POST /v1/entries/{id}/answer` | answer a permission prompt without a WebSocket (notification buttons): `{request_id, allow, answers?}`; 409 if it was already answered |
| `POST /v1/entries/{id}/send` | send a message without a WebSocket (notification reply): `{text}`; 400 if blank, 409 while a terminal holds the session. `{text, from: "session@machine"}` (a session on another of the user's machines) arrives prefixed with who sent it and how to answer. `{text, wait: true}` (a call) holds the request until that turn ends and answers `{sent, reply}` with what Claude said (`reply: null` after 10 minutes) |
| `POST /v1/entries/{id}/upload?name=` | the body is a file's bytes (up to 100 MB; 413 over): saved in the machine's temp folder → `{path}`, which the app puts in the message |
| `POST /v1/entries/{id}/withdraw` | `{text}` → `{withdrawn}`: take back a message sent mid-turn that Claude hasn't read yet (cancelled in Claude Code's queue by uuid); `false` once it's read, for one with images, or one this daemon didn't send |
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
| `bash` | `entry, command`: a `!command`. It runs in the session's folder on that machine (up to 2 minutes; 30k characters of each of stdout and stderr kept) and is added to the conversation as Claude Code records it (`<bash-input>…</bash-input><bash-stdout>…</bash-stdout><bash-stderr>…</bash-stderr>`) without starting a turn, so Claude sees it with the next message. Clients get it as a `message` |
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
| `plan` | `account, plan: {five_hour, seven_day: {used (%), resets_at}}`: the plan's limits (Claude Code's `get_usage`), after each turn |
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
- `/btw` side chats (Claude Code's own `/btw` isn't available through the SDK): `{type: btw, entry, text}` forks the session (`fork_session`, titled "Switchboard /btw side chat", kept out of `/v1/sessions`) and asks there with no tools; follow-ups reuse the fork. Events `{type: btw, entry, kind: asked|delta|answer|done|closed}`. `{type: btw_close, entry}` deletes the fork. The session's own history never sees it; a new session with nothing said yet gets error `nothing_to_ask_about`.
- `/clear` (or any reset) makes Claude Code start a new session id: the daemon moves the entry to it and broadcasts `{type: session_reset, entry, session_id}` (clients empty the chat). History and resumes then use the new session; the old transcript stays on disk.
- Storage: one SQLite file in the daemon's data dir (entries, watches, inbox, client tokens).


## Relay (private repo `cyaninc-lab/switchboard-relay`)

A machine signed in to a relay keeps one WebSocket open to it; apps reach it at `<relay>/d/<machine id>` with the same API
as above, authenticated by their own account sign-in (the relay vouches for the device to the daemon). The relay's own
API: `POST /v1/signin {name, kind, screen}` → GitHub device code; `POST /v1/signin/{id}` (poll) → the device and its
secret; `GET /v1/me`, `GET /v1/devices`, `POST /v1/devices/{id}/approve`, `DELETE /v1/devices/{id}`; `POST /v1/push`
(a machine's notice) and `GET /v1/push/json?since=<id>` (a phone's notification stream, ndjson, a keepalive every 4 min).
