"""The web app (the same Svelte UI as the desktop app), served by the daemon so a phone or another computer's browser
can use Switchboard over Tailscale. The files are public (open source); everything under /v1 still needs a paired token."""
import os, sys
from pathlib import Path
from aiohttp import web


def find():
    """The built web app: SWITCHBOARD_WEBUI, else bundled in the frozen daemon, else desktop/build in a checkout."""
    for d in (os.environ.get("SWITCHBOARD_WEBUI"),
              getattr(sys, "_MEIPASS", None) and Path(sys._MEIPASS) / "webui",
              Path(__file__).resolve().parents[2] / "desktop" / "build"):
        if d and (Path(d) / "index.html").is_file():
            return Path(d).resolve()
    return None


def handler(root):
    async def serve(request):
        if root is None:
            return web.Response(status=404, text="Switchboard daemon: the web app isn't bundled with this one.")
        rel = request.match_info.get("tail", "")
        f = (root / rel).resolve()
        if root not in f.parents and f != root:  # no ../ out of the web app's folder
            raise web.HTTPNotFound()
        if not f.is_file():
            f = root / "index.html"  # the app's own routes (it's a single page)
        cache = "public, max-age=31536000, immutable" if "/_app/immutable/" in f.as_posix() else "no-cache"
        return web.FileResponse(f, headers={"Cache-Control": cache})
    return serve
