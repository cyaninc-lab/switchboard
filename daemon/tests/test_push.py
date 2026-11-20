"""Phone notifications: the daemon posts to each subscribed phone topic (a fake ntfy here), and a phone can answer
or reply over plain HTTP."""
import asyncio, json, os, sys
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_live import setup, until


def test_push_permission_answer_finish_and_reply(tmp_path, monkeypatch):
    async def t():
        got = asyncio.Queue()

        async def topic(request):  # what ntfy would receive
            await got.put(json.loads(await request.read()))
            return web.json_response({"id": "x"})
        ntfy = web.Application(); ntfy.router.add_post("/{topic}", topic)
        fake = TestServer(ntfy); await fake.start_server()

        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        bad = await client.put("/v1/push", headers=h, json={"subscribe": str(fake.make_url("/sb-1"))})  # no machine_url
        assert bad.status == 400
        r = await client.put("/v1/push", headers=h, json={"server": "http://yagoo:2586/", "subscribe": str(fake.make_url("/sb-1")),
                                                          "machine_url": "http://fantome:7878/"})
        assert await r.json() == {"server": "http://yagoo:2586", "subscribers": 1}

        # a permission prompt reaches the phone, with what it needs to answer
        await ws.send_json({"type": "send", "entry": e["id"], "text": "run the tests"})
        req, _ = await until(ws, lambda m: m["type"] == "permission_request")
        note = await asyncio.wait_for(got.get(), 5)
        while note["kind"] != "tool":
            note = await asyncio.wait_for(got.get(), 5)
        assert note["machine"] == "http://fantome:7878" and note["entry"] == e["id"] and note["request_id"] == req["request_id"]
        assert note["title"].endswith("needs you") and note["body"].startswith("Allow Bash")

        # Allow from the notification (plain HTTP); the phone hears it's resolved
        r = await client.post(f"/v1/entries/{e['id']}/answer", headers=h, json={"request_id": req["request_id"], "allow": True})
        assert (await r.json()) == {"answered": True}
        assert (await client.post(f"/v1/entries/{e['id']}/answer", headers=h, json={"request_id": req["request_id"], "allow": True})).status == 409
        kinds = []
        while "finished" not in kinds:  # resolved, then the turn ends
            kinds.append((await asyncio.wait_for(got.get(), 5))["kind"])
        assert "resolved" in kinds

        # a reply from the notification goes to the session
        r = await client.post(f"/v1/entries/{e['id']}/send", headers=h, json={"text": "thanks"})
        assert r.status == 200
        m, _ = await until(ws, lambda m: m["type"] == "message" and "thanks" in json.dumps(m))
        assert (await client.post(f"/v1/entries/{e['id']}/send", headers=h, json={"text": " "})).status == 400

        # unsubscribed: nothing more is posted; and no token, no answering
        await client.put("/v1/push", headers=h, json={"unsubscribe": str(fake.make_url("/sb-1"))})
        assert (await (await client.get("/v1/push", headers=h)).json())["subscribers"] == 0
        assert (await client.post(f"/v1/entries/{e['id']}/send", json={"text": "x"})).status == 401
        await client.close(); await fake.close()
    asyncio.run(t())
