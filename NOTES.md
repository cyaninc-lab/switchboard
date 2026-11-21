## Switchboard 0.1.26

- **Questions no longer get lost:** a question or permission request asked while your phone (or any app) wasn't connected now shows when you open it. Before, the session sat waiting on a question you couldn't see, and new messages queued behind it. The ■ interrupt button now also shows while a session waits on a question.
- **Messages sent while Claude works go in straight away:** Claude reads them after the step it's on (a command finishing), as in Claude Code, instead of after its whole reply.
- **Fewer, better notifications:**
  - The phone stays quiet while you're in Switchboard on a computer (its window in front, used in the last 3 minutes). A question still unanswered a minute after you leave goes to the phone then. On by default: Settings → Phone notifications.
  - With the phone app open, only the chat on screen is kept quiet; other sessions still notify you.
  - The desktop tells you when a session you left finishes.
- **Sessions on different machines can message each other** (`name@machine`). Turn it on in Settings → "Let sessions on my machines message each other".
- **A machine the app can't reach is marked "can't reach"**, with Retry, instead of looking empty.
- **Machines without the desktop app update themselves** when their daemon runs with `--auto-update`.
- Claude's own notes about images it reads no longer show as messages from you.
- Fixes: tools like `adb` work in sessions run by the background service; updating the app also updates the background service.
