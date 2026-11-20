"""Phone notifications: the daemon publishes to an ntfy server; the Switchboard phone app listens on its own topic.

Setting `push_server` (e.g. http://yagoo:2586) only has to be on one machine: the phone app reads it from any machine
it is paired with and subscribes its topic on all of them (`push_subscribers`). A message carries what a notification
shows (session name, what it needs) and the ids the app needs to act on it; the app decides what the lock screen shows.
"""
import asyncio
import json
import logging

import aiohttp

SERVER, SUBSCRIBERS = "push_server", "push_subscribers"
log = logging.getLogger(__name__)


def subscribers(store):
    """A phone's topic URL -> the address that phone reaches this daemon at (a notification names it, to act on)."""
    return json.loads(store.setting(SUBSCRIBERS, "{}"))


def set_subscribers(store, subs):
    store.set_setting(SUBSCRIBERS, json.dumps(subs))


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
    """POST the notice to every subscribed phone topic; never raises (a phone being away mustn't break the daemon)."""
    subs = subscribers(store)
    if not subs:
        return

    async def one(s, u, machine):
        async with s.post(u, data=json.dumps({**note, "machine": machine}).encode()) as r:
            if r.status >= 300:
                log.warning("push to a phone failed: HTTP %s", r.status)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            for r in await asyncio.gather(*(one(s, u, m) for u, m in subs.items()), return_exceptions=True):
                if isinstance(r, Exception):
                    log.warning("push to a phone failed: %s", type(r).__name__)
    except Exception as e:  # ponytail: no retry; ntfy caches for the phone, a lost post loses one notification
        log.warning("push failed: %s", type(e).__name__)
