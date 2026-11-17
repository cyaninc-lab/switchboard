## Switchboard 0.1.6

- **Background work keeps its session open.** Switchboard closes sessions after 10 quiet minutes (they reopen on your next message). A session with a background subagent or command still running is no longer closed, since closing it would stop that work.
- (from 0.1.5) Replies Claude starts on its own appear as they happen; fixes replies arriving one message behind.
