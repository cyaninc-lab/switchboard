"""switchboard-daemon: serve | pair | clients | revoke <hash prefix>"""
import argparse, asyncio, os, time
from aiohttp import web
from .app import make_app
from .store import Store, default_data_dir


def _alive(pid):
    if os.name == "nt":  # never os.kill(pid, 0) on Windows: it sends a real ctrl-C
        import ctypes
        k = ctypes.WinDLL("kernel32")
        h = k.OpenProcess(0x101000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if not h:
            return False
        running = k.WaitForSingleObject(h, 0) == 258  # WAIT_TIMEOUT
        k.CloseHandle(h)
        return running
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _watch(pid):
    """Embedded mode: the app may be closed, killed or crash; whichever way it goes, the daemon goes too."""
    async def loop():
        while _alive(pid):
            await asyncio.sleep(1)
        os._exit(0)
    asyncio.get_running_loop().create_task(loop())


def main():
    p = argparse.ArgumentParser(prog="switchboard-daemon")
    p.add_argument("--data", default=os.environ.get("SWITCHBOARD_DATA", default_data_dir()), help="data directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the daemon")
    s.add_argument("--host", default="127.0.0.1", help="listen address (localhost by default)")
    s.add_argument("--port", type=int, default=7878)
    s.add_argument("--name", help="machine name shown in clients (default: hostname)")
    s.add_argument("--exit-with", type=int, metavar="PID", help="exit when this process ends (the desktop app passes its own pid)")
    s.add_argument("--demo", action="store_true", help="serve canned demo sessions instead of this machine's (UI tests, screenshots)")
    sub.add_parser("pair", help="print a one-time pairing code (10 minutes, single use)")
    sub.add_parser("clients", help="list paired clients")
    r = sub.add_parser("revoke", help="revoke a client by the start of its token hash")
    r.add_argument("prefix")
    a = p.parse_args()

    store = Store(os.path.join(a.data, "switchboard.db"))
    if a.cmd == "serve":
        if a.host not in ("127.0.0.1", "localhost", "::1"):
            # ponytail: TLS lands before any non-localhost use is recommended; until then, a private network only
            print(f"warning: listening on {a.host} without TLS; use a private network (LAN/VPN) only")
        if a.demo:
            from . import app as appmod, demo
            appmod.sdk = demo  # ponytail: swaps only the session readers the API uses; phase 2 running sessions will need a demo runner too
            appmod.open_in_terminal = lambda: {"demo-2"}
        app = make_app(store, a.name or ("demo" if a.demo else None))
        if a.exit_with:
            app.on_startup.append(lambda _app: _watch(a.exit_with))
        web.run_app(app, host=a.host, port=a.port, shutdown_timeout=2)  # stop promptly; clients reconnect
    elif a.cmd == "pair":
        print(store.new_pair_code())
    elif a.cmd == "clients":
        for c in store.clients():
            print(f"{c['token_hash'][:10]}  {c['name']}  paired {time.strftime('%Y-%m-%d %H:%M', time.localtime(c['created']))}")
    elif a.cmd == "revoke":
        print(f"revoked {store.revoke(a.prefix)} client(s)")


if __name__ == "__main__":
    main()
