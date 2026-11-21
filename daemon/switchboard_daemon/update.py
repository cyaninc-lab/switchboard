"""Self-update for daemons installed with pip (headless machines with no desktop app to update them).

`serve --auto-update` checks GitHub's latest release every few hours. When it's newer, it waits until no session is
busy, pip-installs that release into its own environment and exits; the service manager (systemd `Restart=always`)
starts the new version. The bundled daemon never does this: the desktop app updates it."""
import asyncio, logging, os, signal, sys
import aiohttp
from . import __version__
from .github import API, REPO

log = logging.getLogger("switchboard.update")
EVERY = 6 * 3600  # between checks
IDLE_POLL = 60  # while waiting for busy sessions to finish


def newer(tag, than=__version__):
    """'v0.1.26' > '0.1.25'? Anything unparseable is not newer."""
    try:
        return tuple(map(int, tag.lstrip("v").split("."))) > tuple(map(int, than.split(".")))
    except ValueError:
        return False


async def latest():
    """The latest release's tag, e.g. 'v0.1.26'."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(f"{API}/repos/{REPO}/releases/latest",
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "Switchboard"}) as r:
            r.raise_for_status()
            return (await r.json())["tag_name"]


def source(tag):
    """What pip installs: the release's source archive (no git needed), daemon folder."""
    return f"switchboard-daemon @ https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz#subdirectory=daemon"


async def install(tag):
    """pip-install a release into this daemon's own environment. True when it worked."""
    p = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "-q", "--upgrade", source(tag),
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await p.communicate()
    if p.returncode:
        log.error("update to %s failed: %s", tag, out.decode(errors="replace")[-2000:])
    return p.returncode == 0


def busy(runner):
    return any(lv.working or lv.pending or lv.queue or runner.running_tasks(lv) for lv in runner.live.values())


async def check(runner, quit=lambda: os.kill(os.getpid(), signal.SIGTERM)):
    """One round: update and quit if a newer release is out. Returns the tag it updated to, else None."""
    tag = await latest()
    if not newer(tag):
        return None
    log.info("Switchboard %s is out (running %s); updating once no session is busy", tag, __version__)
    while busy(runner):
        await asyncio.sleep(IDLE_POLL)
    if not await install(tag):
        return None
    log.info("updated to %s; restarting", tag)
    quit()  # a clean stop; systemd starts the new version
    return tag


async def loop(runner):
    await asyncio.sleep(300)  # not while the machine is still booting
    while True:
        try:
            await check(runner)
        except Exception:
            log.exception("update check")
        await asyncio.sleep(EVERY)
