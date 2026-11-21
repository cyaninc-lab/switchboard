## Switchboard 0.1.29

- **Sessions work like Claude Code in the terminal:** they now start with Claude Code's own instructions, and get your terminal's PATH, so plugins that need it (like ponytail) and tools such as `node` and `npm` work in sessions. Expect sessions to be terser and use less of your plan.
- **Stop is cheaper:** it stops what Claude is doing and keeps the session open. Closing a session (⋯ → Close session) makes the next message re-read its whole history, which costs a lot on a long session.
- The background service no longer keeps old app folders on its PATH.
