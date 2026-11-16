"""switchboard-daemon: serve | pair | clients | revoke <hash prefix>"""
import argparse, os, time
from aiohttp import web
from .app import make_app
from .store import Store, default_data_dir


def main():
    p = argparse.ArgumentParser(prog="switchboard-daemon")
    p.add_argument("--data", default=os.environ.get("SWITCHBOARD_DATA", default_data_dir()), help="data directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the daemon")
    s.add_argument("--host", default="127.0.0.1", help="listen address (localhost by default)")
    s.add_argument("--port", type=int, default=7878)
    s.add_argument("--name", help="machine name shown in clients (default: hostname)")
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
        web.run_app(make_app(store, a.name), host=a.host, port=a.port)
    elif a.cmd == "pair":
        print(store.new_pair_code())
    elif a.cmd == "clients":
        for c in store.clients():
            print(f"{c['token_hash'][:10]}  {c['name']}  paired {time.strftime('%Y-%m-%d %H:%M', time.localtime(c['created']))}")
    elif a.cmd == "revoke":
        print(f"revoked {store.revoke(a.prefix)} client(s)")


if __name__ == "__main__":
    main()
