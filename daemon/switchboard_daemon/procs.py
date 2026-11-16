"""Is a process still alive, and still the same process (a reused pid doesn't count)? Linux, macOS, Windows."""
import os


def alive(pid, start=None):
    if os.name == "nt":
        return _alive_windows(pid, start)
    try:
        with open(f"/proc/{pid}/stat") as f:  # Linux: field 22 = start time, guards against pid reuse
            stat = f.read().rsplit(")", 1)[1].split()
        return stat[0] != "Z" and (start is None or stat[19] == str(start))
    except FileNotFoundError:
        if os.path.exists("/proc/self"):
            return False
    except (OSError, IndexError):
        return False
    try:  # macOS/BSD: no /proc; ponytail: no start-time check there, add one via sysctl if pid reuse ever bites
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _alive_windows(pid, start):
    # never os.kill(pid, 0) on Windows: it sends a real Ctrl+C
    import ctypes
    from ctypes import byref, c_ulonglong, wintypes
    k = ctypes.WinDLL("kernel32")
    k.OpenProcess.restype = wintypes.HANDLE
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x101000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not h:
        return False
    created, a, b, c = c_ulonglong(), c_ulonglong(), c_ulonglong(), c_ulonglong()
    ok = k.GetProcessTimes(h, byref(created), byref(a), byref(b), byref(c))
    running = k.WaitForSingleObject(h, 0) == 258  # WAIT_TIMEOUT: still running
    k.CloseHandle(h)
    return bool(ok and running and (start is None or str(created.value) == str(start)))


def start_time(pid):
    """The value alive(pid, start) compares against: Linux /proc start ticks, Windows creation time, else None."""
    if os.name == "nt":
        import ctypes
        from ctypes import byref, c_ulonglong, wintypes
        k = ctypes.WinDLL("kernel32")
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        created, a, b, c = c_ulonglong(), c_ulonglong(), c_ulonglong(), c_ulonglong()
        k.GetProcessTimes(h, byref(created), byref(a), byref(b), byref(c))
        k.CloseHandle(h)
        return str(created.value)
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None
