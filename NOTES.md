## Switchboard 0.1.4

- **Fix: long sessions opened at their beginning.** Opening a session (or the app restarting, e.g. after an update) showed only its first few hundred messages and looked as if it had rolled back and stopped updating. It now opens at the latest messages.
- (from 0.1.3) Sessions' Claude processes no longer outlive the app after an update or crash.
