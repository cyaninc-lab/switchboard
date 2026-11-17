## Switchboard 0.1.5

- **Fix: replies one message behind.** When Claude replied on its own (for example when a background command finished), that reply wasn't shown, and every message after it got the previous message's answer. Switchboard now listens all the time, so replies Claude starts by itself appear as they happen.
- (from 0.1.4) Long sessions open at their latest messages.
