# switchboard-daemon

Serves this machine's Claude Code sessions to Switchboard clients. API: `../docs/protocol.md`.

```
pip install ./daemon            # or: pip install -e ./daemon for development
switchboard-daemon serve        # 127.0.0.1:7878 by default; --host/--port/--name
switchboard-daemon pair         # one-time code (10 min) for a client
switchboard-daemon clients      # list paired clients; revoke <hash prefix> to remove one
switchboard-daemon account add work < token.txt   # extra Claude account (token from `claude setup-token`, read from stdin)
switchboard-daemon account list | remove work
```

Data (paired clients, the tree) lives in one SQLite file in the platform data dir
(`~/.local/share/switchboard`, `~/Library/Application Support/switchboard`, `%LOCALAPPDATA%\switchboard`)
or `--data`.

Phase 1 is read-only: pairing, machine info, sessions, tree entries, history. Running and chatting
with sessions comes in phase 2. No TLS yet: keep non-localhost addresses on a private network.

Tests: `python -m pytest daemon/tests`
