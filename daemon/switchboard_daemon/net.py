"""Reachability: the daemon always listens on localhost; optionally also on this machine's Tailscale address,
so your other devices can connect directly (Tailscale encrypts; clients still need a paired token).
The setting persists and can only be changed from the machine itself."""
import asyncio, json, os, shutil, subprocess
from aiohttp import web
from .logs import log

SETTING = "listen_tailscale"


def tailscale_info():
    """{"name", "dns", "ips"} for this machine on its tailnet, or None if Tailscale isn't installed or running."""
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        p = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe")
        exe = p if os.path.exists(p) else None
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if os.name == "nt" else 0)  # no console window on Windows
        me = json.loads(out.stdout or "{}").get("Self") or {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    ips = [ip for ip in me.get("TailscaleIPs") or [] if "." in ip]  # IPv4: what people type
    dns = (me.get("DNSName") or "").rstrip(".")
    return {"name": dns.split(".")[0] if dns else None, "dns": dns or None, "ips": ips} if ips else None


class Network:
    def __init__(self, store):
        self.store, self.runner, self.port = store, None, None
        self.sites, self.active, self.error = [], [], None

    def enabled(self):
        return self.store.setting(SETTING) == "1"

    def attach(self, runner, port):
        self.runner, self.port = runner, port

    async def apply(self):
        """Open or close the Tailscale listener to match the setting, without restarting the daemon."""
        for s in self.sites:
            await s.stop()
        self.sites, self.active, self.error = [], [], None
        if not (self.runner and self.enabled()):
            return
        info = await asyncio.to_thread(tailscale_info)
        if not info:
            self.error = "Tailscale isn't running on this machine"
            return
        for ip in info["ips"]:
            try:
                site = web.TCPSite(self.runner, ip, self.port)
                await site.start()
                self.sites.append(site)
                self.active.append(f"{ip}:{self.port}")
            except OSError as e:
                self.error = f"Couldn't listen on {ip}:{self.port}: {e.strerror or e}"
        if self.active:
            log.info("also listening on %s (Tailscale)", ", ".join(self.active))

    async def status(self):
        info = await asyncio.to_thread(tailscale_info)
        url = f"http://{info['name']}:{self.port}" if info and info.get("name") else None
        return {"listen_tailscale": self.enabled(), "active": self.active, "error": self.error,
                "tailscale": info, "port": self.port, "url": url}
