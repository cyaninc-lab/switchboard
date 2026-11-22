"""Phone notifications: the daemon posts a notice to its account's relay, which passes it to the account's signed-in
phones (one stream per phone; see the relay). A notice carries what a notification shows (session name, what it needs)
and the ids the app needs to act on it, plus this machine's relay address for the buttons; the app decides what the
lock screen shows. Not signed in to a relay: no phone notifications."""
import asyncio
import time
import json
import logging

import aiohttp

VOICE, LISTEN = "voice_server", "listen_server"  # calls: a /say?text= → audio service, an OpenAI-style transcription service
log = logging.getLogger(__name__)


def notice(store, ev, last_state):
    """What to tell the phone about this event, or None. `last_state` is entry id -> state, kept by the caller."""
    t = ev.get("type")
    if t == "permission_request":
        e = store.entry(ev["entry"]) or {}
        kind, inp = ev.get("kind", "tool"), ev.get("input") or {}
        body = ((inp.get("questions") or [{}])[0].get("question") or "Claude has a question") if kind == "question" \
            else "Claude has a plan for you to approve" if kind == "plan" \
            else f"Allow {ev.get('tool')}: {str(inp.get('command') or inp.get('file_path') or inp.get('url') or '')[:200]}".rstrip(": ")
        return {"kind": kind, "entry": ev["entry"], "request_id": ev["request_id"], "name": e.get("name") or "A session",
                "title": f"{e.get('name') or 'A session'} needs you", "body": body,
                "question": (inp.get("questions") or [{}])[0].get("question", "") if kind == "question" else ""}
    if t == "call":  # the phone rings; answering reads `body` aloud
        e = store.entry(ev["entry"]) or {}
        return {"kind": "call", "entry": ev["entry"], "name": e.get("name") or "A session",
                "title": f"{e.get('name') or 'A session'} is calling", "body": ev["message"]}
    if t == "permission_resolved":  # answered somewhere else: the phone takes its notification away
        return {"kind": "resolved", "entry": ev["entry"], "request_id": ev["request_id"]}
    if t == "message" and (ev.get("message") or {}).get("type") == "assistant":  # the last thing Claude said, for "finished"
        content = ((ev["message"].get("message") or {}).get("content")) or []
        text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
        if text:
            last_state[("said", ev["entry"])] = text
        return None
    if t == "state":
        was, now = last_state.get(ev["entry"]), ev.get("state")
        last_state[ev["entry"]] = now
        if was == "working" and now == "ready":
            e = store.entry(ev["entry"]) or {}
            return {"kind": "finished", "entry": ev["entry"], "name": e.get("name") or "A session",
                    "title": f"{e.get('name') or 'A session'} finished", "body": last_state.pop(("said", ev["entry"]), "")[:300] or "Claude is done and waiting for you"}
    if t == "inbox":
        it = ev.get("item") or {}
        return {"kind": "inbox", "entry": it.get("entry_id"), "name": it.get("source") or "Watch",
                "title": it.get("source") or "Switchboard", "body": f"{it.get('title', '')}: {it.get('body', '')}".strip(": ")}
    return None


async def send(store, note):
    """POST the notice to this machine's relay (its account's phones listen there); never raises (a phone being away
    mustn't break the daemon). ponytail: no retry; the relay keeps a day for phones that were away."""
    from . import relay
    if not relay.address(store):
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(store.setting(relay.URL, "") + "/v1/push", json={**note, "machine": relay.address(store)},
                              headers={"Authorization": f"Bearer {store.setting(relay.SECRET, '')}"}) as r:
                if r.status >= 300:
                    log.warning("push through the relay failed: HTTP %s", r.status)
    except Exception as e:
        log.warning("push through the relay failed: %s", type(e).__name__)


PAUSE = "push_pause_at_desk"  # setting: "1" (default) / "0"


class Desk:
    """Phone notifications pause while the user is in a Switchboard desktop window: the app reports {here} while its
    window is focused and touched in the last 3 minutes, every minute. While here, questions are held (sent once the
    user has left for GRACE seconds, if still unanswered) and the rest (finished, inbox) are dropped: they saw it."""
    TTL, GRACE = 90, 60  # a report lasts TTL (the app repeats every 60 s); leaving must last GRACE before held ones go

    def __init__(self, store, send):
        self.store, self.send = store, send
        self.here: dict = {}  # client socket -> when its "here" report runs out
        self.held: dict = {}  # (entry, request_id) -> the question's notice
        self.flush: asyncio.Task | None = None

    def enabled(self):
        return self.store.setting(PAUSE, "1") != "0"

    def present(self, now=None):
        now = now or time.time()
        self.here = {ws: until for ws, until in self.here.items() if until > now}
        return bool(self.here)

    def report(self, ws, here, now=None):
        if here:
            self.here[ws] = (now or time.time()) + self.TTL
        elif self.here.pop(ws, None):
            self._schedule(restart=True)  # left: the wait for the grace period starts now

    def gone(self, ws):  # its socket closed
        if self.here.pop(ws, None):
            self._schedule(restart=True)

    def filter(self, note):
        """The notice to send now, or None (held or dropped)."""
        k = note.get("kind")
        if k == "resolved":
            self.held.pop((note.get("entry"), note.get("request_id")), None)
            return note
        if k == "call" or not self.enabled() or not self.present():
            return note  # a call is Claude asking for you on purpose (rate-limited): it always rings
        if k in ("tool", "question", "plan"):
            self.held[(note["entry"], note["request_id"])] = note
            self._schedule()
        return None

    def _schedule(self, restart=False):
        if restart and self.flush and not self.flush.done():
            self.flush.cancel()  # (not done until the loop runs it: forget it rather than wait)
            self.flush = None
        if self.held and (not self.flush or self.flush.done()):
            try:
                self.flush = asyncio.get_running_loop().create_task(self._flush_when_gone())
            except RuntimeError:
                pass  # no loop (tests calling filter directly)

    async def _flush_when_gone(self):
        while self.held:
            if self.present():
                await asyncio.sleep(min(self.here.values()) - time.time() + 0.1)  # until the soonest report runs out
                continue
            await asyncio.sleep(self.GRACE)
            if self.present():
                continue
            held, self.held = list(self.held.values()), {}
            for n in held:
                await self.send(self.store, n)
