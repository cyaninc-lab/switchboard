"""Cross-machine messages: the app links daemons (peer tokens), then sessions list and message name@machine."""
import asyncio, json, os, sys
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_live import setup, until


def test_link_list_message_and_receive(tmp_path, monkeypatch):
    async def t():
        got = asyncio.Queue()

        async def entries(request):  # the other machine ("yagoo"), as its daemon would answer
            assert request.headers["Authorization"] == "Bearer peer-tok"
            return web.json_response({"entries": [{"id": "e9", "name": "infra agent", "group": "cyaninc-infra", "state": "ready"}]})

        async def send(request):
            await got.put((request.match_info["id"], await request.json()))
            return web.json_response({"sent": True})
        other = web.Application(); other.router.add_get("/v1/entries", entries); other.router.add_post("/v1/entries/{id}/send", send)
        srv = TestServer(other); await srv.start_server()

        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        # the app links the machines: a peer token from this daemon (for the other one), the other's address and token here
        r = await client.post("/v1/peer_token", headers=h, json={"machine": "yagoo"})
        tok = (await r.json())["token"]
        assert store.client_for(tok) == "peer: yagoo"
        r2 = await client.post("/v1/peer_token", headers=h, json={"machine": "yagoo"})  # re-linking replaces it
        assert store.client_for(tok) is None and store.client_for((await r2.json())["token"]) == "peer: yagoo"
        assert (await client.put("/v1/peers", headers=h, json={"peers": [{"name": "yagoo"}]})).status == 400
        r = await client.put("/v1/peers", headers=h, json={"peers": [{"name": "yagoo", "url": str(srv.make_url("")), "token": "peer-tok"}]})
        assert (await r.json())["peers"][0]["name"] == "yagoo" and "token" not in json.dumps(await r.json())

        # a session lists the other machine's sessions and messages one as name@machine
        await ws.send_json({"type": "send", "entry": e["id"], "text": "list sessions"})
        m, _ = await until(ws, lambda m: m["type"] == "message" and "infra agent@yagoo" in json.dumps(m))
        await ws.send_json({"type": "send", "entry": e["id"], "text": "tell infra agent@yagoo: the cover rebuild stays on Fantome"})
        eid, body = await asyncio.wait_for(got.get(), 5)
        assert eid == "e9" and body["text"] == "the cover rebuild stays on Fantome" and body["from"] == e["name"] + "@" + body["from"].rsplit("@", 1)[1]  # "<this session>@<this machine>"
        await until(ws, lambda m: m["type"] == "message" and "Delivered to" in json.dumps(m) and "on yagoo" in json.dumps(m))

        # and the other way: a message from a session on another machine arrives saying where to answer
        r = await client.post(f"/v1/entries/{e['id']}/send", headers=h, json={"text": "done!", "from": "infra agent@yagoo"})
        assert r.status == 200
        await until(ws, lambda m: m["type"] == "message" and 'session \\"infra agent@yagoo\\"' in json.dumps(m) and "done!" in json.dumps(m))
        await client.close(); await srv.close()
    asyncio.run(t())


def test_the_accounts_machines_are_peers(tmp_path, monkeypatch):
    from switchboard_daemon import peers, relay
    from switchboard_daemon.store import Store
    st = Store(str(tmp_path / "s.db"))
    assert asyncio.run(peers.all_peers(st)) == []  # not signed in: only machines linked by hand
    st.set_setting(relay.URL, "https://r.example/relay"); st.set_setting(relay.ID, "me"); st.set_setting(relay.SECRET, "s3")
    devs = [{"id": "me", "name": "Fantome", "kind": "machine", "approved": True, "address": "/d/me", "online": True},
            {"id": "y", "name": "yagoo", "kind": "machine", "approved": True, "address": "/d/y", "online": True},
            {"id": "w", "name": "work", "kind": "machine", "approved": False, "address": "/d/w"},  # not approved yet
            {"id": "p", "name": "Phone", "kind": "app", "approved": True}]
    async def account(store, method="GET", path="/v1/devices"):
        return 200, {"devices": devs}
    monkeypatch.setattr(relay, "account", account)
    monkeypatch.setitem(peers._found, "at", 0)
    peers.set_peers(st, [{"name": "YAGOO", "url": "http://old:7878", "token": "t"}, {"name": "lab", "url": "http://lab:7878", "token": "t2"}])
    ps = asyncio.run(peers.all_peers(st))
    assert [(p["name"], p["url"], p["token"]) for p in ps] == [("yagoo", "https://r.example/relay/d/y", "s3"), ("lab", "http://lab:7878", "t2")]
    assert asyncio.run(peers.find(st, "Yagoo"))["url"].endswith("/d/y")  # the relay wins over the old link
