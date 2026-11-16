"""Daemon log file (rotating) for bug reports. Anything that looks like a secret is scrubbed on the way out."""
import logging, logging.handlers, os, re
from pathlib import Path

log = logging.getLogger("switchboard")
_file = None
SECRETS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-[redacted]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1[redacted]"),
    (re.compile(r'(?i)("?(?:token|secret|password|api_key|authorization)"?\s*[:=]\s*"?)[^"\s,}]{6,}'), r"\1[redacted]"),
]


def scrub(text):
    for rx, sub in SECRETS:
        text = rx.sub(sub, text)
    return text


def setup(data_dir):
    global _file
    d = Path(data_dir) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    _file = d / "daemon.log"
    h = logging.handlers.RotatingFileHandler(_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("switchboard", "aiohttp.server", "aiohttp.web"):
        lg = logging.getLogger(name)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    log.info("daemon starting (pid %s)", os.getpid())


def tail(lines=300):
    """Last lines of the log, scrubbed. Empty if logging isn't set up (tests, demo without serve)."""
    if not _file or not _file.exists():
        return ""
    with open(_file, encoding="utf-8", errors="replace") as f:
        return scrub("".join(f.readlines()[-lines:]))
