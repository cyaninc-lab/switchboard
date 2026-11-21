"""Sessions messaging sessions on the user's other machines.

Daemons don't know each other; the app does. "Let sessions on my machines message each other" asks each daemon for a peer
token (`POST /v1/peer_token`, a client token named "peer: <machine>", revocable like any other) and hands every daemon the
others' addresses and tokens (`PUT /v1/peers`). The switchboard tools then list `name@machine` and deliver over plain HTTP.
"""
import json

import aiohttp

KEY = "peers"
TIMEOUT = aiohttp.ClientTimeout(total=8)


def peers(store):
    return json.loads(store.setting(KEY, "[]"))


def set_peers(store, ps):
    store.set_setting(KEY, json.dumps([{"name": p["name"], "url": p["url"].rstrip("/"), "token": p["token"]} for p in ps]))


def find(store, machine):
    return next((p for p in peers(store) if p["name"].lower() == machine.lower()), None)


async def entries(peer):
    """That machine's tree entries, or None if it can't be reached right now."""
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.get(peer["url"] + "/v1/entries", headers={"Authorization": f"Bearer {peer['token']}"}) as r:
                return (await r.json())["entries"] if r.status == 200 else None
    except Exception:
        return None


async def send(peer, entry_id, text, sender):
    """Deliver to a session there; `sender` is "session@machine", what the reply is addressed to. Returns an error or None."""
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.post(f"{peer['url']}/v1/entries/{entry_id}/send", json={"text": text, "from": sender},
                              headers={"Authorization": f"Bearer {peer['token']}"}) as r:
                return None if r.status == 200 else ((await r.json(content_type=None)) or {}).get("code") or f"HTTP {r.status}"
    except Exception as e:
        return f"can't reach it ({type(e).__name__})"
