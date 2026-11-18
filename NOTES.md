## Switchboard 0.1.12

- **Claude in Chrome works in Switchboard sessions.** Sessions now get the Chrome tools, as they do in a terminal. A session that was already running picks them up after you close it (right-click › Close session) and send your next message.
- **Themes.** Settings › Theme: the four Catppuccin palettes, including Latte, a light theme. Each device remembers its choice.
- **Messages between sessions get their own box.** "✉ To Builder" in the sending session, "✉ From Alpha" in the receiving one, instead of looking like something you typed.
- **Multi-line messages.** Shift+Enter starts a new line; Enter sends.
- **Ctrl+Enter sends now.** While Claude is working, it interrupts and sends your message straight away, as in Claude Code.
- **Queued messages wait at the bottom.** A message sent while Claude is busy stays above the box until it's sent, then lands after the reply it waited for. Esc gives it back to you instead of losing it.
- **Rename from the right-click menu.** Right-click a session to rename it, or a project to rename the project (all its sessions move with it).
- **Delete inbox items.** Each item has a Delete button (it goes from every device).
- **Add several sessions at once.** In Add, click more than one session.
- Fixed: interrupting Claude showed an error saying "None".
- Fixed: a question's label could wrap inside its little pill and squash the question.
