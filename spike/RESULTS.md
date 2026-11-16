# Phase 0 spike results

Run 2026-11-16 on an x86 Linux desktop, claude-agent-sdk 0.2.182 (bundled Claude Code 2.1.325),
model claude-haiku-5-5, signed in with a Claude subscription. Scripts: `spike.py`, `spike_modes.py`.

| Check | Result | Notes |
| --- | --- | --- |
| `list_sessions()` across all projects | pass | sees CLI sessions too |
| Live multi-turn session (`ClaudeSDKClient`) | pass | context kept between turns |
| `can_use_tool` allow | pass | only fires for tools not auto-approved; read-only Bash like `echo` never asks |
| `can_use_tool` deny | pass | denied command not run |
| `interrupt()` mid-command | pass | `spike_interrupt.py`: a 30 s Python command confirmed running, interrupted, killed (its file never appeared), returned in 1.5 s; session usable after draining. The first version used `sleep 25 &&`, which Claude Code blocks outright, so it passed without testing anything (found by reading the session in the Switchboard UI) |
| Close, then resume in a new client | pass | same session id, full context |
| `get_session_messages()` without resuming | pass | |
| Partial streaming (`include_partial_messages`) | pass | token-level deltas for the live view |
| `set_permission_mode()` mid-session | pass | acceptEdits wrote a file without asking |
| Auto mode | pass | ran without asking `can_use_tool` |
| Memory per session | ~240 MiB RSS | one claude process per live session (x86) |
| Resume under a second account | pass | session started on account A resumed with account B's token (`CLAUDE_CODE_OAUTH_TOKEN`) with full context; a bogus token fails with 401, so the token really is used |
| Memory on ARM (Raspberry Pi 5, 16 GB) | ~210 MiB RSS | per live session; with 4.6 GiB free that's roughly 15-20 live sessions, so idle close matters on small boxes |

Lessons for the daemon:

- Strip `CLAUDE*` env vars before spawning sessions, or a session started from inside another Claude session inherits them.
- After `interrupt()`, drain `receive_response()` before the next `query()`.
- Tests of permission flow must use commands Claude Code doesn't auto-approve.
- Spike sessions land in the normal session list (cwd under /tmp); the daemon should tag its own sessions.
