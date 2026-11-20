"""Which apps use the most memory, and closing them. Plain Windows calls through ctypes, no extra packages.

Only apps in your own session are listed, and anything inside the Windows folder is left out, so system processes
can't be ended by accident.
"""
import ctypes
import os
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
version = ctypes.WinDLL("version")

QUERY = 0x1000          # PROCESS_QUERY_LIMITED_INFORMATION
VM_READ = 0x0010
TERMINATE = 0x0001
STILL_ACTIVE = 259
WM_CLOSE = 0x0010

k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]


def memory_status():
    """(percent used, used bytes, total bytes) for the whole computer."""
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.dwMemoryLoad, m.ullTotalPhys - m.ullAvailPhys, m.ullTotalPhys


def _session(pid):
    s = wintypes.DWORD()
    return s.value if k32.ProcessIdToSessionId(pid, ctypes.byref(s)) else None


_names = {}


def app_name(path):
    """The name Windows shows for an app ("Google Chrome"), from the file's description; falls back to the file name."""
    if path in _names:
        return _names[path]
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(path, 0, size, buf):
                ptr, ln = ctypes.c_void_p(), wintypes.UINT()
                if version.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value >= 4:
                    lang, cp = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
                    key = rf"\StringFileInfo\{lang:04x}{cp:04x}\FileDescription"
                    if version.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(ln)) and ln.value > 1:
                        desc = ctypes.wstring_at(ptr, ln.value - 1).strip()
                        if 1 < len(desc) <= 40:
                            name = desc
    except Exception:
        pass
    _names[path] = name
    return name


def top_apps(limit=12, skip=()):
    """Apps using the most memory, biggest first: [{"key", "name", "pids", "bytes"}].
    All processes of the same program are added together (Chrome's many processes become one row)."""
    arr = (wintypes.DWORD * 4096)()
    got = wintypes.DWORD()
    psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(got))
    me = _session(os.getpid())
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower() + "\\"
    apps = {}
    for pid in arr[: got.value // 4]:
        if pid in (0, 4) or pid in skip or _session(pid) != me:
            continue
        h = k32.OpenProcess(QUERY | VM_READ, False, pid) or k32.OpenProcess(QUERY, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                continue
            path = buf.value
            if path.lower().startswith(windir):
                continue
            pmc = PROCESS_MEMORY_COUNTERS_EX()
            pmc.cb = ctypes.sizeof(pmc)
            if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                continue
            used = min(pmc.WorkingSetSize, pmc.PrivateUsage) or pmc.WorkingSetSize   # close to Task Manager's figure
        finally:
            k32.CloseHandle(h)
        key = os.path.basename(path).lower()
        a = apps.setdefault(key, {"key": key, "path": path, "pids": [], "bytes": 0})
        a["pids"].append(pid)
        a["bytes"] += used
    named = {}   # programs with the same name (several "Claude Code" files, say) become one row
    for a in apps.values():
        name = app_name(a["path"])
        b = named.setdefault(name, {"key": name, "name": name, "pids": [], "bytes": 0})
        b["pids"] += a["pids"]
        b["bytes"] += a["bytes"]
    return sorted(named.values(), key=lambda a: -a["bytes"])[:limit]


def windows_of(pids):
    """Visible top-level windows that belong to these processes."""
    pids, found = set(pids), []
    h = user32.GetTopWindow(None)
    while h:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(h):
            found.append(h)
        h = user32.GetWindow(h, 2)   # GW_HWNDNEXT
    return found


def close_app(pids):
    """Asks the app to close, like clicking its X (it can still ask to save). Returns False if it has no window."""
    wins = windows_of(pids)
    for h in wins:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return bool(wins)


def force_end(pids):
    """Ends the processes right away (unsaved work is lost). Returns how many could not be ended."""
    failed = 0
    for pid in pids:
        h = k32.OpenProcess(TERMINATE, False, pid)
        if not h or not k32.TerminateProcess(h, 1):
            failed += 1
        if h:
            k32.CloseHandle(h)
    return failed


def alive(pids):
    """The processes from this list that are still running."""
    left = []
    for pid in pids:
        h = k32.OpenProcess(QUERY, False, pid)
        if h:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == STILL_ACTIVE:
                left.append(pid)
            k32.CloseHandle(h)
    return left


def nice_size(b):
    return f"{b / 1024 ** 3:.1f} GB" if b >= 1024 ** 3 else f"{b / 1024 ** 2:.0f} MB"
