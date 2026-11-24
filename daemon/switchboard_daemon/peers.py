"""Sessions messaging sessions on the user's other machines.

Machines signed in to the same relay account find each other there: no setup, and it works on any network (letters
go through the relay with this machine's own credential; the other daemon trusts it as one of the account's devices).

Otherwise (linked by hand, the older way) daemons don't know each other; the app does. "Let sessions on my machines message each other" asks each daemon for a peer
token (`POST /v1/peer_token`, a client token named "peer: <machine>", revocable like any other) and hands every daemon the
others' addresses and tokens (`PUT /v1/peers`). The switchboard tools then list `name@machine` and deliver over plain HTTP.
"""
import json, time

import aiohttp

from . import relay

KEY = "peers"
TIMEOUT = aiohttp.ClientTimeout(total=8)


def peers(store):
    return json.loads(store.setting(KEY, "[]"))


def set_peers(store, ps):
    store.set_setting(KEY, json.dumps([{"name": p["name"], "url": p["url"].rstrip("/"), "token": p["token"]} for p in ps]))


_found = {"at": 0.0, "list": []}  # the relay account's other machines, asked at most once a minute


async def on_account(store):
    """The other machines on this machine's relay account: [{name, url, token, online}] (the token is this machine's
    relay credential; the relay checks it). [] when not signed in, or the relay can't be asked."""
    if not relay.address(store):
        return []
    if time.time() - _found["at"] > 60:
        try:
            code, d = await relay.account(store)
            devs = d.get("devices", []) if isinstance(d, dict) else d
            if code == 200 and isinstance(devs, list):
                me, base = store.setting(relay.ID, ""), store.setting(relay.URL, "").rstrip("/")
                _found["list"] = [{"name": x["name"], "url": base + x["address"], "token": store.setting(relay.SECRET, ""), "online": x.get("online")}
                                  for x in devs if x.get("kind") == "machine" and x.get("approved") and x.get("address") and x.get("id") != me]
                _found["at"] = time.time()
        except Exception:
            pass  # the last list stands
    return _found["list"]


async def all_peers(store):
    """Every machine this one's sessions can message: its relay account's, then any linked by hand (same name: the relay wins)."""
    found = await on_account(store)
    names = {p["name"].lower() for p in found}
    return found + [p for p in peers(store) if p["name"].lower() not in names]


async def find(store, machine):
    return next((p for p in await all_peers(store) if p["name"].lower() == machine.lower()), None)


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
