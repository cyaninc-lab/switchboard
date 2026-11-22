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
SKIP = {"host", "content-length", "transfer-encoding", "connection", RELAYED.lower()}


async def join(store, url, join_key, name):
    """Register this machine with a relay (needs the relay's join key); returns its address there."""
    url = url.rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(f"{url}/v1/machines", json={"name": name}, headers={"Authorization": f"Bearer {join_key}"}) as r:
            if r.status != 200:
                raise RuntimeError((await r.json(content_type=None) or {}).get("code") or f"HTTP {r.status}")
            d = await r.json()
    store.set_setting(URL, url); store.set_setting(ID, d["id"]); store.set_setting(SECRET, d["secret"])
    return address(store)


def address(store):
    """This machine's address through its relay, or None."""
    url, mid = store.setting(URL, ""), store.setting(ID, "")
    return f"{url}/d/{mid}" if url and mid else None


async def run(store, port):
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
                await _serve(ws, s, port)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("relay tunnel: %s", type(e).__name__)
        await asyncio.sleep(wait)
        wait = min(wait * 2, 60)


async def _serve(ws, session, port):
    local = f"http://127.0.0.1:{port}"
    sockets: dict[int, asyncio.Queue] = {}  # an app's WebSocket: what it sends, in order
    tasks = set()

    async def send(m):
        await ws.send_str(json.dumps(m))

    async def request(m):
        h = {k: v for k, v in (m.get("h") or {}).items() if k.lower() not in SKIP}
        h[RELAYED] = "1"
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
            async with session.ws_connect(local.replace("http", "ws") + m["p"], headers={RELAYED: "1"}, max_msg_size=0) as c:
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
