## Switchboard 0.1.8

- **The tree only lists subagents that are running.** Finished and stopped ones no longer pile up under their session; you can still open one from the "open subagent →" link on the Agent step that started it.
- **You can message a subagent.** Its view now has a message box. Like Claude Code, the message goes through the session that owns the subagent, which passes it on with SendMessage, and resumes the subagent if it had finished.
