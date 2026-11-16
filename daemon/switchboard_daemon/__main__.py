"""switchboard-daemon: serve | pair | clients | revoke <hash prefix>"""
import argparse, asyncio, os, time
from aiohttp import web
from .app import make_app
from .store import Store, default_data_dir


from .procs import alive as _alive  # noqa: E402  (kept as _alive: tests import it)


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
    acc = sub.add_parser("account", help="extra Claude accounts for sessions (token read from stdin, never the command line)")
    acc.add_argument("action", choices=["add", "list", "remove"])
    acc.add_argument("name", nargs="?")
    acc.add_argument("--api-key", action="store_true", help="the secret is an Anthropic API key, not a `claude setup-token` token")
    a = p.parse_args()

    store = Store(os.path.join(a.data, "switchboard.db"))
    if a.cmd == "serve":
        from .logs import setup as setup_logs
        setup_logs(a.data)
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
    elif a.cmd == "account":
        if a.action == "list":
            for x in store.accounts():
                print(f"{x['name']}  ({'API key' if x['kind'] == 'api_key' else 'setup-token'})")
        elif not a.name:
            p.error("account add/remove needs a name")
        elif a.action == "add":
            import getpass, sys
            secret = sys.stdin.readline() if not sys.stdin.isatty() else getpass.getpass("Token (from `claude setup-token`): ")
            store.add_account(a.name, secret, "api_key" if a.api_key else "oauth")
            print(f"added account {a.name}")
        else:
            print("removed" if store.remove_account(a.name) else "no such account")


if __name__ == "__main__":
    main()
