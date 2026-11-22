"""Reaching this daemon through a Switchboard relay: no Tailscale or open port needed, works through any VPN.

The daemon keeps one WebSocket open to the relay (its tunnel). Apps use https://<relay>/d/<id> as this machine's
address; the relay passes their requests and WebSockets through the tunnel, and they're replayed here against the
daemon itself, so every one still needs its own client token. Replayed requests carry RELAYED, so actions allowed only
"from this machine" (pairing codes, network settings) refuse them.
"""
import asyncio, base64, json, logging
import aiohttp

log = logging.getLogger("switchboard.relay")
URL, ID, SECRET = "relay_url", "relay_id", "relay_secret"  # daemon settings (the secret lives with the other tokens)
RELAYED = "X-Switchboard-Relayed"
DEVICE = "X-Switchboard-Device"  # which of the account's approved devices is asking (the relay checked it)
SKIP = {"host", "content-length", "transfer-encoding", "connection", RELAYED.lower(), DEVICE.lower()}


async def signin(store, url, name, show=print):
    """Sign this machine in to a relay with GitHub (the person enters a code at github.com/login/device). Returns its
    address there, or raises. The machine belongs to that GitHub account: its approved devices reach it."""
    url = url.rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(f"{url}/v1/signin", json={"name": name, "kind": "machine"}) as r:
            d = await r.json(content_type=None)
        if "signin" not in d:
            raise RuntimeError(d.get("message") or d.get("code") or f"HTTP {r.status}")
        show(f"Open {d['verification_uri']} and enter {d['user_code']}")
        while True:
            await asyncio.sleep(d.get("interval", 5))
            async with s.post(f"{url}/v1/signin/{d['signin']}") as r:
                p = await r.json(content_type=None)
            if p.get("status") == "waiting":
                continue
            if p.get("status") != "signed_in":
                raise RuntimeError(p.get("error") or p.get("status"))
            break
    dev = p["device"]
    store.set_setting(URL, url); store.set_setting(ID, dev["id"]); store.set_setting(SECRET, p["secret"])
    if not dev["approved"]:
        show("Approve this machine on one of your other devices (Settings → Account) before it can be reached.")
    return address(store)


def address(store):
    """This machine's address through its relay, or None."""
    url, mid = store.setting(URL, ""), store.setting(ID, "")
    return f"{url}/d/{mid}" if url and mid else None


async def run(store, port, host="127.0.0.1"):
    """Keep the tunnel open (reconnecting with backoff) and serve what comes through it."""
    wait = 2
    while address(store):
        url, mid, secret = store.setting(URL, ""), store.setting(ID, ""), store.setting(SECRET, "")
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://") + f"/v1/tunnel/{mid}"
        try:
            async with aiohttp.ClientSession() as s, s.ws_connect(ws_url, headers={"Authorization": f"Bearer {secret}"},
                                                                  heartbeat=25, max_msg_size=0) as ws:
                log.info("relay tunnel up: %s", address(store))
                wait = 2
                await _serve(ws, s, port, host)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("relay tunnel: %s", type(e).__name__)
        await asyncio.sleep(wait)
        wait = min(wait * 2, 60)


async def _serve(ws, session, port, host):
    # the daemon itself, where it listens (yagoo's listens only on its Tailscale address)
    h = "127.0.0.1" if host in ("0.0.0.0", "", None) else ("[::1]" if host == "::" else (f"[{host}]" if ":" in host else host))
    local = f"http://{h}:{port}"
    sockets: dict[int, asyncio.Queue] = {}  # an app's WebSocket: what it sends, in order
    tasks = set()

    async def send(m):
        await ws.send_str(json.dumps(m))

    async def request(m):
        h = {k: v for k, v in (m.get("h") or {}).items() if k.lower() not in SKIP}
        h[RELAYED] = "1"
        h[DEVICE] = json.dumps(m.get("dev") or {})
        try:
            async with session.request(m["m"], local + m["p"], headers=h, data=base64.b64decode(m.get("b") or "")) as r:
                body = await r.read()
                await send({"t": "res", "s": m["s"], "st": r.status, "h": {k: v for k, v in r.headers.items() if k.lower() not in SKIP},
                            "b": base64.b64encode(body).decode()})
        except Exception as e:
            await send({"t": "res", "s": m["s"], "st": 502, "h": {"Content-Type": "application/json"},
                        "b": base64.b64encode(json.dumps({"code": "daemon_unreachable", "message": str(e)[:200]}).encode()).decode()})

    async def socket(m, q: asyncio.Queue):
        """An app's WebSocket: messages queue from the start (the first, its login, can come before this end is open)."""
        s = m["s"]
        try:
            async with session.ws_connect(local.replace("http", "ws") + m["p"], headers={RELAYED: "1", DEVICE: json.dumps(m.get("dev") or {})}, max_msg_size=0) as c:
                async def outbound():
                    while (d := await q.get()) is not None:
                        await c.send_str(d)
                    await c.close()
                out = asyncio.get_running_loop().create_task(outbound())
                try:
                    async for msg in c:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await send({"t": "wsm", "s": s, "d": msg.data})
                finally:
                    out.cancel()
        except Exception:
            pass
        finally:
            if sockets.pop(s, None) is not None:
                try:
                    await send({"t": "wsc", "s": s})
                except Exception:
                    pass

    def spawn(coro):
        t = asyncio.get_running_loop().create_task(coro); tasks.add(t); t.add_done_callback(tasks.discard)

    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            t = m.get("t")
            if t == "req":
                spawn(request(m))
            elif t == "wso":
                q = sockets[m["s"]] = asyncio.Queue()
                spawn(socket(m, q))
            elif t == "wsm" and (q := sockets.get(m["s"])):
                q.put_nowait(m["d"])
            elif t == "wsc" and (q := sockets.pop(m["s"], None)):
                q.put_nowait(None)
    finally:
        for q in list(sockets.values()):
            q.put_nowait(None)
        for t in list(tasks):
            t.cancel()
