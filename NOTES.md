## Switchboard 0.1.9

- **New icons in the session tree, so you can tell what each session is doing at a glance:**
  - green ring: live and idle
  - green spinner: working
  - filled green circle: it replied while you were looking at something else (clears when you open it)
  - flashing green sun: it needs you (a question, a permission, a plan to approve)
- **Drafts stay with their session.** Text and images you haven't sent yet are kept when you switch to another session, and come back when you return.
- **Selecting text in a dialog no longer closes it.** Dragging from a box out past the dialog's edge used to count as a click outside it.
- **Sessions no longer close themselves after 10 quiet minutes.** A live session stays live, so it keeps what Claude Code only remembers while running (like "allow for this session"). To close one, right-click it in the tree and choose **Close session**; it stays in the tree and picks up where it left off on your next message.
- **Sessions can message each other, even stopped ones.** Every session in Switchboard can now list the other sessions on its machine and send one a message by its name in the tree. A stopped session wakes up to read it. (Claude Code's own messaging only reaches sessions that are running.)
- **Old subagents no longer show as running.** Sessions with subagents from weeks ago (made by an older Claude Code) listed them all as running. Now only subagents active since the session last started count.
- **Collapse projects in the tree.** Click a project's name to fold it; a folded project still shows a sun or dot if a session in it needs you or has a reply. Folded projects and subagent lists stay folded after a restart.
