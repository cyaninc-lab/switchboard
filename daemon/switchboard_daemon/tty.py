"""Real-terminal toggle: the actual `claude` TUI for a session, in a pseudo-terminal, streamed over the WebSocket.
Unix only for now (pty); Windows would use ConPTY."""
import asyncio, codecs, fcntl, os, pty, signal, struct, termios


class Terminals:
    def __init__(self, broadcast):
        self.broadcast = broadcast
        self.open: dict[str, dict] = {}  # entry id -> {pid, fd, session_id}

    def held_sessions(self):
        return {t["session_id"] for t in self.open.values()}

    async def start(self, entry_id, session_id, cwd, argv, cols=100, rows=30):
        if entry_id in self.open:
            return
        pid, fd = pty.fork()
        if pid == 0:  # child: become the claude TUI
            try:
                os.chdir(cwd or os.path.expanduser("~"))
            except OSError:
                pass
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLORTERM"] = "truecolor"
            os.execvp(argv[0], argv)
        self._resize(fd, cols, rows)
        os.set_blocking(fd, False)
        t = self.open[entry_id] = {"pid": pid, "fd": fd, "session_id": session_id}
        dec = codecs.getincrementaldecoder("utf-8")("replace")  # a read can split a multi-byte character
        loop = asyncio.get_running_loop()

        def readable():
            try:
                data = os.read(fd, 65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                data = b""
            if not data:
                loop.remove_reader(fd)
                loop.create_task(self._closed(entry_id))
                return
            loop.create_task(self.broadcast({"type": "terminal_output", "entry": entry_id, "data": dec.decode(data)}))
        loop.add_reader(fd, readable)
        await self.broadcast({"type": "terminal_opened", "entry": entry_id})

    def _resize(self, fd, cols, rows):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(cols), 0, 0))

    def write(self, entry_id, data):
        t = self.open.get(entry_id)
        if t:
            os.write(t["fd"], data.encode())

    def resize(self, entry_id, cols, rows):
        t = self.open.get(entry_id)
        if t:
            self._resize(t["fd"], cols, rows)
            os.kill(t["pid"], signal.SIGWINCH)

    async def close(self, entry_id):
        t = self.open.get(entry_id)
        if t:
            try:
                os.kill(t["pid"], signal.SIGHUP)  # like closing a terminal window
            except ProcessLookupError:
                pass

    async def _closed(self, entry_id):
        t = self.open.pop(entry_id, None)
        if t:
            try:
                os.close(t["fd"])
                os.waitpid(t["pid"], os.WNOHANG)
            except OSError:
                pass
            await self.broadcast({"type": "terminal_closed", "entry": entry_id})

    async def close_all(self):
        for eid in list(self.open):
            await self.close(eid)
