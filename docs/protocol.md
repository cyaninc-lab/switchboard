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
| `GET /v1/models` | the models this machine's Claude Code offers (value, displayName, description, effort levels) |
| `GET /v1/folders` | folder suggestions for a new session (where sessions have run, newest first) |
| `PATCH /v1/entries/{id}` | rename, regroup, pin, change account or default mode |
| `DELETE /v1/entries/{id}` | off the tree only; the session stays on disk |
| `GET /v1/entries/{id}/messages?offset=&limit=` | history via `get_session_messages()`, no resume needed |
| `GET /v1/commands` | slash commands for the menu, with descriptions where known |
| `GET/POST/PATCH/DELETE /v1/watches` | watch definitions: source, target entry, instructions, mode, delivery, quiet hours |
| `GET /v1/inbox?since=` · `POST /v1/inbox/{id}/read` | messages from agents and watches |

## WebSocket `/v1/ws`

One socket per client per daemon. JSON messages with a `type`. The first client message must be
`{"type": "auth", "token": ...}` (browsers can't set headers on a WebSocket); the daemon answers `hello`.

Client → daemon:

| type | fields |
| --- | --- |
| `subscribe` | `entries: [id]` (or `"all"`) |
| `send` | `entry, text, images?` (starts or resumes the session if stopped; images = `[{media_type, data(base64)}]`, PNG/JPEG/GIF/WebP, ≤5 MB each, ≤10) |
| `interrupt` / `stop` | `entry` |
| `set_mode` | `entry, mode` (default, acceptEdits, plan, auto, dontAsk, bypassPermissions) |
| `set_model` | `entry, model` (a value from `/v1/models`; null = Claude Code's default; switches a live session) |
| `permission_reply` | `request_id, allow, message?, remember?` |
| `terminal_open` / `terminal_input` / `terminal_resize` / `terminal_close` | real-terminal toggle: PTY running `claude --resume` (the SDK session is closed first) |

Daemon → client:

| type | fields |
| --- | --- |
| `state` | `entry, state` (new, working, waiting, ready = live and idle, stopped, terminal, error) |
| `delta` | `entry, text` (partial streaming) |
| `message` | `entry, message` (full assistant/user/tool message, same shape as history) |
| `permission_request` | `request_id, entry, tool, input` |
| `permission_resolved` | `request_id, allow, by` (first answer wins; every client is told) |
| `inbox` | `item` |
| `terminal_opened` / `terminal_closed` | `entry` (the TUI started / exited) |
| `terminal_output` | `entry, data` |
| `mode` / `model` | `entry, mode` / `entry, model` (after a change) |
| `commands` | `entry, commands` (slash commands the session reported at start) |
| `notice` | `entry, text` (e.g. conversation compacted) |
| `queued` | `entry, count` (a message sent while working waits for the turn to end; interrupt drops the queue) |
| `error` | `entry?, code, message` |

Every subscribed client receives every event, so several clients stay live on one session.

## Daemon behaviour

- One `ClaudeSDKClient` per active entry; closed after the idle timeout (default 10 min),
  resumed on the next `send` or watch.
- Spawned sessions get a clean environment (no inherited `CLAUDE*` vars) plus the entry's
  account token (`CLAUDE_CODE_OAUTH_TOKEN`) when it isn't the machine default.
- Sessions load the user's own settings (`setting_sources` default), so their permission rules apply.
- A session open in a terminal elsewhere shows as `terminal` and refuses `send`.
- Storage: one SQLite file in the daemon's data dir (entries, watches, inbox, client tokens).
