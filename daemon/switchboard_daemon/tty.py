"""Real-terminal toggle: the actual `claude` TUI for a session, in a pseudo-terminal, streamed over the WebSocket.
Unix: pty.fork. Windows: ConPTY through pywinpty (installed on Windows only)."""
import asyncio, codecs, os, threading
from .logs import log


class _UnixPty:
    def __init__(self, argv, cwd, env, cols, rows, on_data, on_exit):
        import fcntl, pty, termios, struct  # noqa: F401  (Unix-only modules)
        self._fcntl, self._termios, self._struct = fcntl, termios, struct
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child: become the claude TUI
            try:
                os.chdir(cwd or os.path.expanduser("~"))
            except OSError:
                pass
            os.environ.update(env)
            os.execvp(argv[0], argv)
        self.resize(cols, rows)
        os.set_blocking(self.fd, False)
        loop = asyncio.get_running_loop()
        dec = codecs.getincrementaldecoder("utf-8")("replace")  # a read can split a multi-byte character

        def readable():
            try:
                data = os.read(self.fd, 65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                data = b""
            if not data:
                loop.remove_reader(self.fd)
                on_exit()
                return
            on_data(dec.decode(data))
        loop.add_reader(self.fd, readable)

    def write(self, data):
        os.write(self.fd, data.encode())

    def resize(self, cols, rows):
        import signal
        self._fcntl.ioctl(self.fd, self._termios.TIOCSWINSZ, self._struct.pack("HHHH", int(rows), int(cols), 0, 0))
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def close(self):
        import signal
        try:
            os.kill(self.pid, signal.SIGHUP)  # like closing a terminal window
        except ProcessLookupError:
            pass

    def reap(self):
        try:
            os.close(self.fd)
            os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            pass


class _WinPty:
    def __init__(self, argv, cwd, env, cols, rows, on_data, on_exit):
        from winpty import PtyProcess  # pywinpty: ConPTY
        self.p = PtyProcess.spawn(argv, cwd=cwd or os.path.expanduser("~"), env={**os.environ, **env}, dimensions=(int(rows), int(cols)))
        loop = asyncio.get_running_loop()

        def pump():  # PtyProcess.read blocks: read on a thread, hand chunks to the event loop
            while True:
                try:
                    data = self.p.read(65536)
                except EOFError:
                    break
                except Exception:
                    log.exception("terminal read failed")
                    break
                if data:
                    loop.call_soon_threadsafe(on_data, data)
            loop.call_soon_threadsafe(on_exit)
        threading.Thread(target=pump, daemon=True).start()

    def write(self, data):
        self.p.write(data)

    def resize(self, cols, rows):
        self.p.setwinsize(int(rows), int(cols))

    def close(self):
        try:
            self.p.terminate(force=True)
        except Exception:
            pass

    def reap(self):
        pass


class Terminals:
    def __init__(self, broadcast):
        self.broadcast = broadcast
        self.open: dict[str, dict] = {}  # entry id -> {pty, session_id}

    def held_sessions(self):
        return {t["session_id"] for t in self.open.values()}

    async def start(self, entry_id, session_id, cwd, argv, cols=100, rows=30, env=None):
        if entry_id in self.open:
            return
        loop = asyncio.get_running_loop()
        env = {**(env or {}), "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        if not (cwd and os.path.isdir(cwd)):  # the session's folder may be gone (or on another OS): start at home
            cwd = os.path.expanduser("~")
        backend = _WinPty if os.name == "nt" else _UnixPty
        pty = backend(argv, cwd, env, cols, rows,
                      on_data=lambda d: loop.create_task(self.broadcast({"type": "terminal_output", "entry": entry_id, "data": d})),
                      on_exit=lambda: loop.create_task(self._closed(entry_id)))
        self.open[entry_id] = {"pty": pty, "session_id": session_id}
        log.info("terminal for entry %s started: %s in %s", entry_id, " ".join(argv[:2]), cwd)
        await self.broadcast({"type": "terminal_opened", "entry": entry_id})

    def write(self, entry_id, data):
        t = self.open.get(entry_id)
        if t:
            t["pty"].write(data)

    def resize(self, entry_id, cols, rows):
        t = self.open.get(entry_id)
        if t:
            t["pty"].resize(cols, rows)

    async def close(self, entry_id):
        t = self.open.get(entry_id)
        if t:
            t["pty"].close()

    async def _closed(self, entry_id):
        t = self.open.pop(entry_id, None)
        if t:
            log.info("terminal for entry %s ended", entry_id)
            t["pty"].reap()
            await self.broadcast({"type": "terminal_closed", "entry": entry_id})

    async def close_all(self):
        for eid in list(self.open):
            await self.close(eid)
