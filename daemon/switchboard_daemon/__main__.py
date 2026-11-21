"""switchboard-daemon: serve | pair | clients | revoke <hash prefix> | account | update"""
import argparse, asyncio, os, signal, sys, time
from aiohttp import web
from .app import make_app
from .store import Store, default_data_dir


from .procs import alive as _alive  # noqa: E402  (kept as _alive: tests import it)


def _exit_with_children():
    """Exit and take this daemon's Claude processes with it. Without this they outlive us, holding their sessions
    (shown as "open in terminal" by the next daemon); Windows has no parent-death signal, and updating the app ends
    it without the app's own tree-kill running."""
    import subprocess, sys
    if sys.platform == "win32":  # /T: our whole tree, ourselves last
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())], capture_output=True, creationflags=0x08000000)
    elif os.getpgrp() in (os.getpid(), os.getppid()):  # the app made us (or our onefile launcher) a group leader
        os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(0)


async def _watch(pid):
    """Embedded mode: the app may be closed, killed or crash; whichever way it goes, the daemon goes too."""
    async def loop():
        while _alive(pid):
            await asyncio.sleep(1)
        _exit_with_children()
    asyncio.get_running_loop().create_task(loop())


async def _serve(app, host, port):
    """Like web.run_app, but keeps the runner so the Tailscale listener can be opened and closed while running."""
    from .app import NET
    runner = web.AppRunner(app, shutdown_timeout=2)  # stop promptly; clients reconnect
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(f"======== Running on http://{host}:{port} ========", flush=True)
    app[NET].attach(runner, port)
    await app[NET].apply()
    stop = asyncio.Event()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    except (NotImplementedError, AttributeError, RuntimeError):
        pass  # Windows: Ctrl+C arrives as KeyboardInterrupt instead
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


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
    s.add_argument("--auto-update", action="store_true", help="install new releases by itself, then exit for the service manager to restart it (pip installs only)")
    sub.add_parser("update", help="install the latest release now if it's newer (pip installs only; restart the service after)")
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
            appmod.subagent_tree = demo.subagent_tree
            from . import net
            net.tailscale_info = lambda: {"name": "demo-box", "dns": "demo-box.tail.ts.net", "ips": ["127.0.0.2"]}  # never the real tailnet
        app = make_app(store, a.name or ("demo" if a.demo else None))
        if a.exit_with:
            app.on_startup.append(lambda _app: _watch(a.exit_with))
        if a.auto_update and not getattr(sys, "frozen", False):  # the bundled daemon is the desktop app's to update
            from . import update
            from .app import RUNNER
            async def auto(app):
                app["update_task"] = asyncio.get_running_loop().create_task(update.loop(app[RUNNER]))
            app.on_startup.append(auto)
        try:
            asyncio.run(_serve(app, a.host, a.port))
        except KeyboardInterrupt:
            pass
    elif a.cmd == "update":
        from . import update, __version__
        if getattr(sys, "frozen", False):
            sys.exit("This daemon came with the desktop app; the app updates it.")
        tag = asyncio.run(update.latest())
        if not update.newer(tag):
            print(f"Up to date ({__version__})")
        elif asyncio.run(update.install(tag)):
            print(f"Installed {tag}; restart the daemon to use it (systemctl --user restart switchboard-daemon)")
        else:
            sys.exit(f"Installing {tag} failed")
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
            import getpass
            secret = sys.stdin.readline() if not sys.stdin.isatty() else getpass.getpass("Token (from `claude setup-token`): ")
            store.add_account(a.name, secret, "api_key" if a.api_key else "oauth")
            print(f"added account {a.name}")
        else:
            print("removed" if store.remove_account(a.name) else "no such account")


if __name__ == "__main__":
    main()
