## Switchboard 0.1.7

- **Subagents in the tree.** When a session starts subagents, they appear nested under it, and subagents they start appear under them, to any depth. Running ones show live, and the list opens by itself while any are running. Click one to see what it's doing (read-only, since subagents can't be messaged). An Agent call in a session also has an "open subagent →" link.
- **Updating on Windows** no longer stops with "Error opening file for writing: switchboard-daemon.exe" (the installer now closes the background helper first).
- **New session:** a folder suggestion that loads late no longer gets mixed into the folder you're typing.
