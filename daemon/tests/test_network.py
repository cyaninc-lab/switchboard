"""The Tailscale listener (faked as 127.0.0.2, a second loopback address) and the local-only guard."""
import asyncio, os, sys, unittest.mock
import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switchboard_daemon import app as appmod, net
from test_live import setup


@pytest.mark.skipif(sys.platform != "linux", reason="127.0.0.2 is bindable without setup on Linux only")
def test_listen_on_tailscale_toggles_live(tmp_path, monkeypatch):
    async def t():
        monkeypatch.setattr(net, "tailscale_info", lambda: {"name": "testbox", "dns": "testbox.tail.ts.net", "ips": ["127.0.0.2"]})
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        port = client.server.port
        client.server.app[appmod.NET].attach(client.server.runner, port)
        far = f"http://127.0.0.2:{port}/v1/machine"

        async def reach():
            try:
                async with aiohttp.ClientSession() as s, s.get(far, timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status
            except aiohttp.ClientConnectorError:
                return "refused"

        assert await reach() == "refused"
        st = await (await client.put("/v1/network", headers=h, json={"listen_tailscale": True})).json()
        assert st["listen_tailscale"] and st["active"] == [f"127.0.0.2:{port}"] and st["url"] == f"http://testbox:{port}"
        assert await reach() == 401  # the daemon answers there now (and still wants a token)
        assert store.setting(net.SETTING) == "1"  # persisted for the next start
        code = (await (await client.post("/v1/pair_code", headers=h)).json())["code"]
        assert store.redeem(code, "other device")
        st = await (await client.put("/v1/network", headers=h, json={"listen_tailscale": False})).json()
        assert not st["listen_tailscale"] and st["active"] == []
        assert await reach() == "refused"
        await client.close()
    asyncio.run(t())


def test_no_tailscale_reports_why(tmp_path, monkeypatch):
    async def t():
        monkeypatch.setattr(net, "tailscale_info", lambda: None)
        client, store, token, h, e, ws = await setup(tmp_path, monkeypatch)
        client.server.app[appmod.NET].attach(client.server.runner, client.server.port)
        st = await (await client.put("/v1/network", headers=h, json={"listen_tailscale": True})).json()
        assert st["active"] == [] and "isn't running" in st["error"] and st["tailscale"] is None
        await client.close()
    asyncio.run(t())


def test_only_this_machine_can_change_reachability():
    transport = unittest.mock.Mock()
    transport.get_extra_info.side_effect = lambda k, d=None: ("100.64.1.2", 51000) if k == "peername" else d
    req = make_mocked_request("PUT", "/v1/network", transport=transport)
    with pytest.raises(web.HTTPForbidden):
        appmod._local_only(req)
    transport.get_extra_info.side_effect = lambda k, d=None: ("127.0.0.1", 51000) if k == "peername" else d
    appmod._local_only(make_mocked_request("PUT", "/v1/network", transport=transport))  # no error
