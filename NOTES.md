## Switchboard 0.1.3

- **Fix: running sessions showed "open in terminal" after an update.** When the app closed without its usual shutdown (updating, a crash, being killed), the sessions' Claude processes kept running on their own and held on to their sessions. The background helper now takes them with it. (The update *to* 0.1.3 still runs the old shutdown code, so it's from the next update on.)
