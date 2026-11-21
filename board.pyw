"""Desk Board - a task board that sits on the desktop in place of the wallpaper.

Write a task in the strip at the bottom and press Enter.
  3pm / 14:30 at the start  -> reminder (a note pops up over your windows when it is due)
  #tag anywhere             -> groups cards (Tab picks a suggestion)
  click a card              -> scratch it off (click again to bring it back)
  drag                      -> move
  double-click              -> edit
  right-click a card        -> delete it (Undo appears); Shift+right-click -> card options
  right-click the board     -> tidy up, clear, shortcuts
"hide" in the top-right corner tucks the board away; Alt + ` or the Start menu ("Desk Board") brings it back.
The board never closes by itself: a tiny keeper process starts it again if it ever stops.
Each day gets its own file in ./boards; unfinished cards move to the next day.
"""
import ctypes
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import winsound
from ctypes import wintypes
from pathlib import Path

APP = Path(os.environ.get("DESKBOARD_DATA") or Path(__file__).resolve().parent)
ERROR_LOG = APP / "error.log"


def log_error(text):
    try:
        if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 200_000:   # keep it small
            ERROR_LOG.unlink()
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {text.rstrip()}\n")
    except OSError:
        pass


def keeper():
    """Runs the board and starts it again if it ever stops on its own (crash, killed, Windows hiccup).
    Uses almost no memory: it only waits for the board process."""
    import time
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = wintypes.HANDLE
    k.OpenEventW.restype = wintypes.HANDLE
    k.SetEvent.argtypes = [wintypes.HANDLE]
    keeper.mutex = k.CreateMutexW(None, False, "DeskBoard_Keeper")
    if k.GetLastError() == 183:                        # already running: ask the board to come forward
        ev = k.OpenEventW(0x0002, False, "DeskBoard_Show")
        if ev:
            k.SetEvent(ev)
        return
    quick = 0
    while True:
        started = time.time()
        code = subprocess.call([sys.executable, str(Path(__file__).resolve()), "--child"])
        if code == 0:                                  # closed on purpose (or another board is already open)
            return
        log_error(f"Desk Board stopped (exit code {code}), starting it again.")
        quick = quick + 1 if time.time() - started < 60 else 0
        if quick >= 5:
            log_error("It stopped 5 times in a row within a minute, so the keeper gave up. Start it from the Start menu.")
            return
        time.sleep(2)


if __name__ == "__main__" and "--child" not in sys.argv and not os.environ.get("DESKBOARD_TEST"):
    keeper()
    sys.exit(0)

if sys.stderr is None:          # pythonw has no console: send any error text to error.log instead
    class _Log:
        def write(self, t):
            if t.strip():
                log_error(t)

        def flush(self):
            pass
    sys.stderr = _Log()
BOARDS = APP / "boards"
BOARDS.mkdir(parents=True, exist_ok=True)
SETTINGS = APP / "settings.json"
IMAGES = APP / "images"
IMAGES.mkdir(parents=True, exist_ok=True)
CLIP_PS1 = Path(__file__).resolve().parent / "clip_image.ps1"
QUOTES = Path(__file__).resolve().parent / "quotes.txt"
TABS_PS1 = Path(__file__).resolve().parent / "tabs.ps1"
TAB_MEM_RE = re.compile(r"\s-\s(?:high memory usage|memory usage)\s-\s([\d.,]+)\s?([KMG])B", re.I)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import procs  # noqa: E402  (memory panel)
TEST = bool(os.environ.get("DESKBOARD_TEST"))

# ---------------------------------------------------------------- windows plumbing
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

_SUFFIX = "_test" if os.environ.get("DESKBOARD_TEST") else ""
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_mutex = kernel32.CreateMutexW(None, False, "DeskBoard_SingleInstance" + _SUFFIX)
if kernel32.GetLastError() == 183:
    # already running (e.g. the taskbar icon was clicked): ask that board to come to the front
    ev = kernel32.OpenEventW(0x0002, False, "DeskBoard_Show" + _SUFFIX)  # EVENT_MODIFY_STATE
    if ev:
        kernel32.SetEvent(ev)
    sys.exit(0)
SHOW_EVENT = kernel32.CreateEventW(None, False, False, "DeskBoard_Show" + _SUFFIX)  # auto-reset
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
HOTKEY_TEXT = "Alt + `"
MOD_BITS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
KEY_CODES = {"`": 0xC0, "esc": 0x1B, "space": 0x20, "tab": 0x09, "enter": 0x0D, "-": 0xBD, "=": 0xBB,
             "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC}
DEFAULT_HOTKEYS = {"board": "Alt + `", "memory": "Alt + Shift + `"}


def parse_hotkey(text):
    """"Ctrl + Alt + M" -> (modifiers, virtual key), or None if it can't be read."""
    parts = [x.strip().lower() for x in re.split(r"\s*\+\s*(?=.)", text.strip()) if x.strip()]
    if not parts:
        return None
    mods = 0
    for m in parts[:-1]:
        if m not in MOD_BITS:
            return None
        mods |= MOD_BITS[m]
    k = parts[-1]
    if k in KEY_CODES:
        vk = KEY_CODES[k]
    elif len(k) == 1 and k.isalnum():
        vk = ord(k.upper())
    elif re.fullmatch(r"f([1-9]|1[0-2])", k):
        vk = 0x6F + int(k[1:])
    else:
        return None
    return mods, vk

user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_GetLong = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_SetLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_GetLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetLong.restype = ctypes.c_ssize_t
_SetLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetLong.restype = ctypes.c_ssize_t


def work_area():
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


# ---------------------------------------------------------------- look
DESK = "#1f1f1d"
DESK_HI = "#2a2a27"
RULE = "#3b3a36"
SHADOW = "#141413"
ON_DESK = "#ebe8e1"
ON_DESK_2 = "#a19d94"
ON_DESK_3 = "#67645d"
PAPER = "#f0ede6"
PAPER_DONE = "#d6d2c9"
PAPER_DIM = "#3a3935"
INK = "#1c1b19"
INK_2 = "#57534b"
INK_3 = "#8f8a80"
INK_DIM = "#5f5c56"
RED = "#d9442a"
MUSTARD = "#a8740f"
PALETTE = [("#3563d6", "#ffffff"), ("#2e8a57", "#ffffff"), ("#8b55c7", "#ffffff"), ("#e3a72b", "#1c1b19"),
           ("#1f8a8a", "#ffffff"), ("#d4497f", "#ffffff"), ("#7f912c", "#ffffff"), ("#5b6b80", "#ffffff")]

PASTELS = ["#f4ecd6", "#dcefe2", "#e0e7fa", "#f8dfe8", "#ece0f8", "#d9eff0"]  # cards with no tag
GREEN = "#43c47e"
FLAME = "#ff7a45"
ACCENT = "#3563d6"
KEY = "#010203"  # transparent colour for rounded pop-ups

TAG_RE = re.compile(r"(^|\s)#([\w-]+)", re.U)


# ---------------------------------------------------------------- time helpers
def now_hm():
    n = dt.datetime.now()
    return f"{n.hour:02d}:{n.minute:02d}"


def to_min(hm):
    h, m = map(int, hm.split(":"))
    return h * 60 + m


def from_min(v):
    v = max(0, min(1439, v))
    return f"{v // 60:02d}:{v % 60:02d}"


def nice_time(hm):
    h, m = map(int, hm.split(":"))
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def dur(n):
    return f"{n} min" if n < 60 else f"{n // 60} h" + (f" {n % 60} min" if n % 60 else "")


def parse_clock(s):
    """'3pm', '3:30 pm', '15:30', '9' -> 'HH:MM' or None."""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*", s, re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def parse_task(raw):
    """'3pm #mumbai pause the list' -> (time, tags, text)"""
    time, rest = None, raw.strip()
    m = (re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?\s+(.+)$", rest, re.I | re.S)
         or re.match(r"^(\d{1,2})()\s*(am|pm)\s+(.+)$", rest, re.I | re.S))
    if m:
        t = parse_clock(f"{m.group(1)}:{m.group(2) or '00'}{m.group(3) or ''}")
        if t:
            time, rest = t, m.group(4)
    tags = []
    for g in (x.group(2).lower() for x in TAG_RE.finditer(rest)):
        if g not in tags:
            tags.append(g)
    lines = [(" " * (len(ln) - len(ln.lstrip(" "))) if LIST_RE.match(ln) else "") + re.sub(r"[ \t]+", " ", ln).strip()
             for ln in TAG_RE.sub(lambda m: m.group(1), rest).split("\n")]
    lines = [ln if ln.strip() else "" for ln in lines]
    text = "\n".join(ln for ln in lines if ln) or ("Untitled" if tags else raw.strip())
    return time, tags, text


# Slack-style formatting: *bold*  _italic_  ~strike~  `code`, lines starting "- " are bullets
def _wrap_re(ch):
    e = re.escape(ch)
    return rf"(?<![\w{e}]){e}(?=\S)[^{e}\n]+?(?<=\S){e}(?![\w{e}])"


FMT_RE = re.compile("|".join([_wrap_re("*"), _wrap_re("_"), _wrap_re("~"), r"`[^`\n]+`"]))
STYLE_OF = {"*": "b", "_": "i", "~": "s", "`": "code"}
LIST_RE = re.compile(r"^( *)(\d{1,2}[.)]|[a-z][.)]|[-*•◦])\s")
INDENT = "   "


def next_marker(mk):
    if mk[0].isdigit():
        return f"{int(mk[:-1]) + 1}{mk[-1]}"
    if mk[0].isalpha():
        return (chr(ord(mk[0]) + 1) if mk[0] < "z" else "z") + mk[-1]
    return mk


def runs(line):
    """'call *Ravi* now' -> [('call ', ''), ('Ravi', 'b'), (' now', '')]"""
    out, pos = [], 0
    for m in FMT_RE.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], ""))
        out.append((m.group(0)[1:-1], STYLE_OF[m.group(0)[0]]))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], ""))
    return out


def plain(text):
    return FMT_RE.sub(lambda m: m.group(0)[1:-1], text)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- the board
class DeskBoard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Desk Board")
        self.root.overrideredirect(True)
        self.left, self.top, self.W, self.H = work_area()
        self.root.geometry(f"{self.W}x{self.H}+{self.left}+{self.top}")
        self.root.configure(bg=DESK)
        self.k = self.root.winfo_fpixels("1i") / 96.0

        self.f_day = tkfont.Font(family="Bahnschrift SemiBold Condensed", size=40)
        self.f_mono = tkfont.Font(family="Cascadia Mono", size=9)
        self.f_mono_s = tkfont.Font(family="Cascadia Mono", size=8)
        self.f_ui = tkfont.Font(family="Bahnschrift", size=10)
        self.f_quote = tkfont.Font(family="Georgia", size=12, slant="italic")
        self.f_quote_mark = tkfont.Font(family="Georgia", size=30, weight="bold")
        self.f_card = tkfont.Font(family="Bahnschrift", size=11)
        self.f_card_b = tkfont.Font(family="Bahnschrift", size=11, weight="bold")
        self.f_card_i = tkfont.Font(family="Bahnschrift", size=11, slant="italic")
        self.f_code = tkfont.Font(family="Cascadia Mono", size=10)
        self.f_input_b = tkfont.Font(family="Bahnschrift", size=12, weight="bold")
        self.f_input_i = tkfont.Font(family="Bahnschrift", size=12, slant="italic")
        self.f_input_code = tkfont.Font(family="Cascadia Mono", size=11)
        self.f_tab = tkfont.Font(family="Bahnschrift SemiBold", size=8)
        self.f_stamp = tkfont.Font(family="Bahnschrift SemiBold Condensed", size=8)
        self.f_input = tkfont.Font(family="Bahnschrift", size=12)
        self.f_empty = tkfont.Font(family="Bahnschrift SemiBold Condensed", size=20)

        self.CW = self.s(250)
        self.c = tk.Canvas(self.root, bg=DESK, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)

        self.settings = load_json(SETTINGS, {})
        self.settings.setdefault("tags", {})
        self.settings.setdefault("filter", [])
        if "hotkeys" not in self.settings:   # written to settings.json so it's easy to find and change
            self.settings["hotkeys"] = dict(DEFAULT_HOTKEYS)
            save_json(SETTINGS, self.settings)
        self.date = None
        self.tasks = []
        self.load_today()

        self.front = False
        self.img_cache = {}
        self.regen = set()
        self.pending_images = []
        self.pasting = 0
        self.press = None
        self.click_job = None
        self.editor = None
        self.popup = None
        self.undo_snap = None
        self.reminded = set()
        self.reminder_wins = []
        self.top_safe = self.s(150)

        self.build_input()
        self.build_badge()
        self.render()

        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        ex = _GetLong(self.hwnd, -20)
        _SetLong(self.hwnd, -20, (ex | 0x80) & ~0x40000)  # tool window: not in taskbar / Alt+Tab
        self.push_bottom()

        c = self.c
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Button-3>", self.on_right)
        c.bind("<Shift-Button-3>", lambda e: self.on_right(e, card_menu=True))
        c.bind("<Key>", self.on_canvas_key)
        self.root.bind("<FocusIn>", lambda e: self.root.after(15, self.push_bottom))
        self.root.bind("<Escape>", lambda e: self.send_back())
        self.root.protocol("WM_DELETE_WINDOW", self.send_back)   # Alt+F4 doesn't close the board
        self.root.report_callback_exception = self.on_error
        self.hidden = False

        # Alt + ` (the key above Tab) brings the board up over everything; press again to send it back
        self.hotkey_hit = False
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        self.root.after(150, self.watch_signals)

        self.root.after(1000, self.every_second)
        self.root.after(20000, self.every_20s)
        self.root.after(1500, self.check_reminders)
        if TEST:
            self.root.attributes("-topmost", True)
            self.root.after(int(os.environ.get("DESKBOARD_TEST")), self.quit)
            demo = os.environ.get("DESKBOARD_DEMO_TEXT")
            if demo:
                self.root.after(1200, lambda: (self.focus_entry(), self.hide_placeholder(),
                                               self.entry.insert("1.0", demo.replace("|", chr(10))), self.after_type(self.entry)))

    def s(self, v):
        return int(round(v * self.k))

    # ------------------------------------------------ window behaviour
    def push_bottom(self):
        if TEST or self.front:
            return
        # HWND_BOTTOM | SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        user32.SetWindowPos(self.hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def hotkey_loop(self):
        # global shortcuts; runs on its own thread with its own message queue
        keys = self.settings.get("hotkeys") or {}
        self.hotkeys_ok = {}
        for hid, which in ((1, "board"), (2, "memory")):
            hk = parse_hotkey(keys.get(which) or DEFAULT_HOTKEYS[which])
            self.hotkeys_ok[which] = bool(hk) and bool(user32.RegisterHotKey(None, hid, hk[0] | 0x4000, hk[1]))  # 0x4000 = no repeat
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.hotkey_hit = int(msg.wParam)

    def watch_signals(self):
        if self.hotkey_hit:
            hit, self.hotkey_hit = self.hotkey_hit, False
            if hit == 2:                                  # memory shortcut: board up + memory panel open
                if self.popup is not None and self.popup is getattr(self, "mem_popup", None):
                    self.close_popup()
                    self.send_back()
                else:
                    self.bring_front()
                    self.mem_panel()
            else:
                self.send_back() if self.front else self.bring_front()
        if kernel32.WaitForSingleObject(SHOW_EVENT, 0) == 0:  # taskbar / Start menu icon clicked
            self.bring_front()
        self.root.after(150, self.watch_signals)

    def on_error(self, kind, err, tb):
        import traceback
        log_error("".join(traceback.format_exception(kind, err, tb)))

    def hide(self):
        """Tuck the board away (it keeps running). Alt + ` or the Start menu brings it back."""
        self.close_popup()
        self.save()
        self.front = False
        self.hidden = True
        user32.ShowWindow(self.hwnd, 0)

    def bring_front(self):
        self.front = True
        self.hidden = False
        user32.ShowWindow(self.hwnd, 5)
        user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # topmost for a moment
        user32.SetWindowPos(self.hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # then normal, still on top
        user32.SetForegroundWindow(self.hwnd)
        self.root.focus_force()
        self.focus_entry()

    def send_back(self):
        if self.editor or self.popup:
            return
        self.front = False
        self.push_bottom()
        self.c.focus_set()

    def every_second(self):
        if self.front and user32.GetForegroundWindow() != self.hwnd and not self.popup:
            self.front = False  # you switched to another app
            self.push_bottom()
        if not self.hidden and (user32.IsIconic(self.hwnd) or not user32.IsWindowVisible(self.hwnd)):  # Win+D
            user32.ShowWindow(self.hwnd, 4)
            self.push_bottom()
        self.c.itemconfigure("clock", text=dt.datetime.now().strftime("%H:%M"))
        self.ticks = getattr(self, "ticks", 0) + 1
        if self.ticks % 5 == 0:
            self.update_mem_pill()
        self.badge_tick()
        self.root.after(1000, self.every_second)

    def every_20s(self):
        if dt.date.today().isoformat() != self.date:
            self.load_today()
            self.render()
        elif not self.press and not self.editor and not self.popup:
            self.render()
        self.check_reminders()
        self.root.after(20000, self.every_20s)

    # ------------------------------------------------ storage
    def board_path(self, day):
        return BOARDS / f"{day}.json"

    def load_today(self):
        today = dt.date.today().isoformat()
        self.date = today
        f = self.board_path(today)
        if f.exists():
            self.tasks = load_json(f, {"tasks": []}).get("tasks", [])
        else:
            prev = sorted(p for p in BOARDS.glob("*.json") if p.stem < today)
            old = load_json(prev[-1], {"tasks": []}).get("tasks", []) if prev else []
            self.tasks = [dict(t, tags=list(t.get("tags", []))) for t in old if not t.get("done")]
            self.save()
        # keep today plus the last two days; older day files are deleted
        keep_from = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        for p in BOARDS.glob("*.json"):
            if p.stem < keep_from:
                try:
                    p.unlink()
                except OSError:
                    pass
        used = set()
        for p in BOARDS.glob("*.json"):
            for t in load_json(p, {"tasks": []}).get("tasks", []):
                used.update(t.get("images", []))
        cutoff = dt.datetime.now().timestamp() - 86400
        for f in IMAGES.glob("*.png"):
            if f.stem.split("_")[0] not in used and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass

    def save(self):
        save_json(self.board_path(self.date), {"date": self.date, "tasks": self.tasks})
        save_json(SETTINGS, self.settings)

    def find(self, tid):
        return next((t for t in self.tasks if t["id"] == tid), None)

    def next_id(self):
        return max([t["id"] for t in self.tasks], default=0) + 1

    def snapshot(self):
        self.undo_snap = (json.dumps(self.tasks), json.dumps(self.settings))

    def undo(self):
        if self.undo_snap:
            self.tasks = json.loads(self.undo_snap[0])
            self.settings = json.loads(self.undo_snap[1])
            self.undo_snap = None
            self.save()
            self.render()

    # ------------------------------------------------ tags
    def ink(self, name):
        tags = self.settings["tags"]
        if name not in tags:
            used = list(tags.values())
            i = 0
            while used.count(i) > len(used) // len(PALETTE):
                i += 1
            tags[name] = i % len(PALETTE)
        return PALETTE[tags[name] % len(PALETTE)]

    def usage(self):
        n = {}
        for t in self.tasks:
            for g in t.get("tags", []):
                n[g] = n.get(g, 0) + 1
        return n

    def known_tags(self):
        n = self.usage()
        return sorted(self.settings["tags"], key=lambda g: (-n.get(g, 0), g))

    def visible(self, t):
        f = self.settings["filter"]
        return not f or any(g in f for g in t.get("tags", []))

    def set_filter(self, lst):
        self.settings["filter"] = lst
        self.save()
        self.render()

    def toggle_filter(self, g):
        f = self.settings["filter"]
        self.set_filter([x for x in f if x != g] if g in f else f + [g])

    # ------------------------------------------------ state of a card
    def state_of(self, t):
        if t.get("done"):
            return "done"
        if not t.get("time"):
            return ""
        if t.get("since", self.date) < self.date:
            return "late"
        diff = to_min(t["time"]) - to_min(now_hm())
        return "late" if diff < 0 else "due" if diff <= 15 else ""

    def rel_label(self, t):
        if t.get("done") or not t.get("time") or t.get("since", self.date) < self.date:
            return ""
        diff = to_min(t["time"]) - to_min(now_hm())
        if diff == 0:
            return "now"
        return f"in {dur(diff)}" if diff > 0 else f"{dur(-diff)} ago"

    def from_label(self, since):
        d = dt.date.fromisoformat(since)
        days = (dt.date.fromisoformat(self.date) - d).days
        if days == 1:
            return "from yesterday"
        return f"from {d.strftime('%A')}" if days < 7 else f"from {d.day} {d.strftime('%b')}"

    # ------------------------------------------------ shapes
    @staticmethod
    def rr_pts(x1, y1, x2, y2, r):
        r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r,
                x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
                x1, y1 + r, x1, y1]

    def rrect(self, x1, y1, x2, y2, r, **kw):
        return self.c.create_polygon(self.rr_pts(x1, y1, x2, y2, r), smooth=True, splinesteps=12, **kw)

    def top_round(self, x1, y1, x2, y2, r, **kw):
        pts = [x1, y2, x1, y2, x1, y1 + r, x1, y1 + r, x1, y1, x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1,
               x2, y1, x2, y1 + r, x2, y1 + r, x2, y2, x2, y2]
        return self.c.create_polygon(pts, smooth=True, splinesteps=12, **kw)

    @staticmethod
    def mix(a, b, k):
        pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{int(x + (y - x) * k):02x}" for x, y in zip(pa, pb))

    def card_colour(self, t):
        tags = t.get("tags") or []
        if tags:
            base = self.ink(tags[0])[0]
            return self.mix(base, "#ffffff", 0.78), self.mix(base, INK, 0.35)
        return PASTELS[t["id"] % len(PASTELS)], INK_2

    # ------------------------------------------------ streak (a habit nudge)
    def bump_streak(self):
        st = self.settings.setdefault("streak", {"count": 0, "last": None})
        if st["last"] == self.date:
            return
        y = (dt.date.fromisoformat(self.date) - dt.timedelta(days=1)).isoformat()
        st["count"] = st["count"] + 1 if st["last"] == y else 1
        st["last"] = self.date

    # ------------------------------------------------ drawing
    def render(self):
        c, s = self.c, self.s
        c.delete("ui")
        self.update_badge()
        W, H = self.W, self.H
        self.settings["filter"] = [f for f in self.settings["filter"] if f in self.settings["tags"]]
        today = dt.date.fromisoformat(self.date)

        # day, date, clock
        day = c.create_text(s(36), s(14), anchor="nw", text=today.strftime("%A"), font=self.f_day, fill=ON_DESK, tags="ui")
        b = c.bbox(day)
        d = c.create_text(b[2] + s(16), b[3] - s(12), anchor="sw", text=f"{today.day} {today.strftime('%B')}",
                          font=self.f_mono, fill=ON_DESK_2, tags="ui")
        c.create_text(c.bbox(d)[2] + s(12), b[3] - s(12), anchor="sw", text=dt.datetime.now().strftime("%H:%M"),
                      font=self.f_mono, fill=ON_DESK, tags=("ui", "clock"))

        # progress: one coloured bead per card, filled when scratched off
        shown = [t for t in self.tasks if self.visible(t)]
        done = sum(1 for t in shown if t.get("done"))
        y0 = b[3] + s(10)
        x = s(38)
        if shown and len(shown) <= 30:
            for t in sorted(shown, key=lambda t: not t.get("done")):
                col = (self.ink(t["tags"][0])[0] if t.get("tags") else GREEN) if t.get("done") else "#3a3935"
                self.rrect(x, y0 + s(4), x + s(18), y0 + s(12), s(4), fill=col, outline="", tags="ui")
                x += s(22)
            x += s(8)
        if shown:
            txt = f"{done} of {len(shown)} done"
            carried = sum(1 for t in shown if t.get("since", self.date) < self.date and not t.get("done"))
            if carried:
                txt += f"   ·   {carried} from yesterday"
        else:
            txt = "No cards with this tag today" if self.settings["filter"] else "Nothing planned yet"
        st = c.create_text(x, y0 + s(8), anchor="w", text=txt, font=self.f_ui, fill=ON_DESK_2, tags="ui")
        x = c.bbox(st)[2] + s(16)

        streak = self.settings.get("streak") or {}
        yday = (today - dt.timedelta(days=1)).isoformat()
        if streak.get("last") == self.date:
            n = streak.get("count", 1)
            lt = c.create_text(x + s(12), y0 + s(8), anchor="w", text=f"{n} day streak", font=self.f_tab, fill="#ffffff", tags="ui")
            lb = c.bbox(lt)
            pill = self.rrect(x, y0 - s(3), lb[2] + s(12), y0 + s(19), s(11), fill=FLAME, outline="", tags="ui")
            c.tag_lower(pill, lt)
        elif streak.get("last") == yday and streak.get("count"):
            c.create_text(x, y0 + s(8), anchor="w", text=f"scratch one off to keep your {streak['count']} day streak",
                          font=self.f_ui, fill="#ffb38a", tags="ui")
        self.top_safe = y0 + s(58)
        hdr_right = c.bbox("ui")[2]

        # hide
        ht = c.create_text(W - s(42), s(22), anchor="ne", text="hide  ✕", font=self.f_ui, fill=ON_DESK_2, tags=("ui", "act_hide"))
        hb = c.bbox(ht)
        hit = self.rrect(hb[0] - s(12), hb[1] - s(6), hb[2] + s(12), hb[3] + s(6), s(14), fill=DESK_HI, outline="", tags=("ui", "act_hide"))
        c.tag_lower(hit, ht)

        # clear (only when there is something to clear)
        if self.tasks:
            ct = c.create_text(hb[0] - s(34), s(22), anchor="ne", text="clear", font=self.f_ui, fill=ON_DESK_2,
                               tags=("ui", "act_clear"))
            cb = c.bbox(ct)
            cbg = self.rrect(cb[0] - s(12), cb[1] - s(6), cb[2] + s(12), cb[3] + s(6), s(14), fill=DESK_HI, outline="",
                             tags=("ui", "act_clear"))
            c.tag_lower(cbg, ct)
            self.clear_at = (cb[0] - s(12), cb[3] + s(10))
            pills_left = cb[0] - s(12)
        else:
            pills_left = hb[0] - s(12)
        pills_left = self.draw_mem_pill(pills_left - s(10))
        pills_left = self.draw_assistant_pill(pills_left - s(10))

        self.quote_bottom = self.draw_quote(hdr_right + s(56), pills_left - s(56))
        self.draw_filter()

        all_done = bool(shown) and done == len(shown)
        if all_done:
            self.confetti()

        for t in self.tasks:
            if t.get("fx") is None:
                fx, fy = self.free_spot()
                t["fx"], t["fy"] = fx / W, fy / H
                self.save()
        for t in self.tasks:
            self.draw_card(t)

        if not self.tasks:
            c.create_text(W // 2, H // 2 - s(20), text="Fresh desk. What is first today?", font=self.f_empty, fill=ON_DESK, tags="ui")
            c.create_text(W // 2, H // 2 + s(16), text="Write it below.  For example   3pm #mumbai check pickup rate",
                          font=self.f_ui, fill=ON_DESK_3, tags="ui")
        elif all_done:
            c.create_text(W // 2, self.H - s(150), text="All done for today. Nice work.", font=self.f_empty, fill=ON_DESK, tags="ui")

        self.draw_strip_back()
        self.update_input_context()
        self.update_preview()
        c.tag_raise("inputwin")

    def confetti(self):
        import random
        rnd = random.Random(self.date)
        cols = [p[0] for p in PALETTE] + [FLAME, GREEN]
        for _ in range(70):
            x = rnd.randint(self.s(60), self.W - self.s(60))
            y = rnd.randint(self.top_safe, self.H - self.s(120))
            w, h = rnd.choice([(8, 8), (10, 4), (4, 10), (6, 6)])
            self.rrect(x, y, x + self.s(w), y + self.s(h), self.s(2), fill=rnd.choice(cols), outline="", tags="ui")

    # ------------------------------------------------ quote of the day
    def today_quote(self):
        if getattr(self, "_quote", (None,))[0] != self.date:
            try:
                rows = [ln.split("|", 1) for ln in QUOTES.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.startswith("#") and "|" in ln]
            except OSError:
                rows = []
            q = None
            if rows:
                import random
                order = list(range(len(rows)))
                random.Random(len(rows)).shuffle(order)     # fixed shuffle: no repeats until every quote has shown
                text, who = rows[order[dt.date.fromisoformat(self.date).toordinal() % len(rows)]]
                q = (text.strip(), who.strip())
            self._quote = (self.date, q)
        return self._quote[1]

    def draw_quote(self, left, right):
        c, s = self.c, self.s
        self.quote_right = 0
        q = self.today_quote()
        width = min(right - left, s(640))
        if not q or width < s(260):
            return 0
        cx = (left + right) / 2
        tx = c.create_text(cx, s(26), anchor="n", text=q[0], width=width, justify="center",
                           font=self.f_quote, fill="#d9d4ca", tags=("ui", "quote"))
        b = c.bbox(tx)
        c.create_text(b[0] - s(6), b[1] - s(4), anchor="ne", text="“", font=self.f_quote_mark, fill=FLAME,
                      tags=("ui", "quote"))
        w = c.create_text(cx, b[3] + s(8), anchor="n", text="—  " + q[1], font=self.f_mono_s, fill=ON_DESK_3,
                          tags=("ui", "quote"))
        self.quote_right = cx + width / 2
        return c.bbox(w)[3]

    def draw_filter(self):
        c, s = self.c, self.s
        n = self.usage()
        in_use = [g for g in self.known_tags() if n.get(g) or g in self.settings["filter"]]
        if not in_use:
            return
        f = self.settings["filter"]
        items = [("All", len(self.tasks), None, (ON_DESK, INK), not f)] + [(g, n.get(g, 0), g, self.ink(g), g in f) for g in in_use]
        widths = [(s(16) if g else 0) + self.f_ui.measure(lab) + s(7) + self.f_mono_s.measure(str(cnt)) + s(28)
                  for lab, cnt, g, ink, on in items]
        gap = s(8)
        x = self.W - s(30) - sum(widths) - gap * (len(items) - 1)
        y = max(s(64), self.quote_bottom + s(14)) if x < self.quote_right else s(64)
        self.top_safe = max(self.top_safe, y + s(46))
        for (lab, cnt, g, ink, on), w in zip(items, widths):
            act = ("ui", "act_filter", f"tag:{g}") if g else ("ui", "act_all")
            self.rrect(x, y, x + w, y + s(30), s(15), fill=ink[0] if on else DESK_HI, outline="", tags=act)
            tx = x + s(14)
            if g:
                dot = ink[1] if on else ink[0]
                c.create_oval(tx, y + s(11), tx + s(8), y + s(19), fill=dot, outline="", tags=act)
                tx += s(16)
            lt = c.create_text(tx, y + s(15), anchor="w", text=lab, font=self.f_ui, fill=ink[1] if on else ON_DESK, tags=act)
            c.create_text(c.bbox(lt)[2] + s(7), y + s(15), anchor="w", text=str(cnt), font=self.f_mono_s,
                          fill=self.mix(ink[1], ink[0], 0.35) if on else ON_DESK_3, tags=act)
            x += w + gap

    def card_xy(self, t):
        s = self.s
        x = max(s(10), min(t["fx"] * self.W, self.W - self.CW - s(10)))
        y = max(self.top_safe, min(t["fy"] * self.H, self.H - s(210)))
        return int(x), int(y)

    def free_spot(self):
        s = self.s
        ch = s(120)
        gx, gy = self.CW + s(34), ch + s(48)
        cols = max(1, (self.W - s(60)) // gx)
        x0 = max(s(10), (self.W - cols * gx) // 2 + s(17))
        taken = [self.card_xy(t) for t in self.tasks if t.get("fx") is not None]
        y = self.top_safe
        while y < self.H - s(220):
            for col in range(cols):
                x = x0 + col * gx
                if not any(abs(px - x) < self.CW + s(10) and abs(py - y) < ch + s(20) for px, py in taken):
                    return x, y
            y += gy
        k = len(self.tasks)
        return x0 + (k * s(28)) % s(280), self.top_safe + (k * s(28)) % s(160)

    def draw_card(self, t):
        c, s, CW = self.c, self.s, self.CW
        tid = t["id"]
        tag = f"c{tid}"
        T = ("ui", "card", tag)
        x, y = self.card_xy(t)
        st = self.state_of(t)
        vis = self.visible(t)
        done = st == "done"
        paper, meta = self.card_colour(t)
        ink = INK
        if done:
            paper, meta, ink = self.mix(paper, "#8f8c85", 0.55), "#6f6b63", "#55514a"
        if not vis:
            paper, meta, ink = "#33322e", "#55534d", "#605d57"
        P, R = s(16), s(16)

        shadow = self.rrect(x + s(2), y + s(5), x + CW + s(2), y + s(60), R, fill="#151514", outline="", tags=T)
        tabs_start = None
        # tag tabs: rounded tops peeking out above the card
        rx = x + CW - s(18)
        for g in reversed(t.get("tags", [])):
            bg, fg = self.ink(g)
            if not vis:
                bg, fg = "#4a4844", "#8a867e"
            elif done:
                bg = self.mix(bg, "#8f8c85", 0.5)
            tw = self.f_tab.measure(g) + s(18)
            tg = T + ("act_tab", f"tag:{g}")
            self.top_round(rx - tw, y - s(20), rx, y + s(6), s(8), fill=bg, outline="", tags=tg)
            c.create_text(rx - tw / 2, y - s(9), text=g, font=self.f_tab, fill=fg, tags=tg)
            rx -= tw + s(4)
        body = self.rrect(x, y, x + CW, y + s(56), R, fill=paper, outline="", tags=T)

        # first line: time or "added", relative time, carried, stamp
        if t.get("time"):
            label = nice_time(t["time"])
            col = RED if st == "late" else "#9a6a00" if st == "due" else meta
        elif t.get("created"):
            label, col = "added " + nice_time(t["created"]), self.mix(meta, paper, 0.25)
        else:
            label, col = "set a time", self.mix(meta, paper, 0.25)
        tm = c.create_text(x + P, y + P, anchor="nw", text=label, font=self.f_mono, fill=col, tags=T + ("act_time",))
        lx = c.bbox(tm)[2] + s(8)
        extra = []
        r = self.rel_label(t)
        if r:
            extra.append((r, col))
        if t.get("since", self.date) < self.date:
            extra.append((self.from_label(t["since"]), meta))
        stamp_on = st in ("due", "late") and vis
        limit = x + CW - P - s(24)                      # copy icon
        if stamp_on:
            limit -= self.f_stamp.measure("LATE" if st == "late" else "SOON") + s(30)
        for txt, ecol in extra:
            if lx + self.f_mono.measure(txt) > limit:   # no room: leave it out rather than overlap
                break
            it = c.create_text(lx, y + P, anchor="nw", text=txt, font=self.f_mono, fill=ecol, tags=T)
            lx = c.bbox(it)[2] + s(8)
        if st in ("due", "late") and vis:
            sbg, sfg = (RED, "#ffffff") if st == "late" else ("#f2b33d", INK)
            sp = c.create_text(x + CW - P - s(32), y + P + s(1), anchor="ne", text="LATE" if st == "late" else "SOON",
                               font=self.f_stamp, fill=sfg, tags=T)
            sb = c.bbox(sp)
            pill = self.rrect(sb[0] - s(8), sb[1] - s(2), sb[2] + s(8), sb[3] + s(2), s(9), fill=sbg, outline="", tags=T)
            c.tag_lower(pill, sp)

        # copy button, top-right inside the card
        ix, iy = x + CW - P - s(7), y + P + s(8)
        CT = T + ("act_copy", f"cp{tid}")
        self.rrect(ix - s(12), iy - s(12), ix + s(12), iy + s(12), s(7), fill=paper, outline="", tags=CT)
        self.rrect(ix - s(6), iy - s(7), ix + s(3), iy + s(3), s(2), fill="", outline=meta, width=max(1, s(1.4)), tags=CT)
        self.rrect(ix - s(3), iy - s(4), ix + s(6), iy + s(6), s(2), fill=paper, outline=meta, width=max(1, s(1.4)), tags=CT)

        ty = c.bbox(tm)[3] + s(7)
        if t["text"].strip():
            bottom, extents = self.draw_rich(x + P, ty, CW - 2 * P - s(4), t["text"], ink, paper, T)
        else:
            bottom, extents = ty - s(4), []
        if t.get("images"):
            bottom = self.draw_images(x + P, bottom + s(8), CW - 2 * P, t["images"], paper, T)
        y2 = bottom + P
        c.coords(body, *self.rr_pts(x, y, x + CW, y2, R))
        c.coords(shadow, *self.rr_pts(x + s(2), y + s(5), x + CW + s(2), y2 + s(5), R))

        if done:  # red marker scratch through each line, and a green tick
            wob = [0, -1.4, 1.0, -0.8, 1.2, 0]
            for x1, x2, ly in extents:
                x1, x2 = x1 - s(3), x2 + s(3)
                pts = []
                for j, w in enumerate(wob):
                    pts += [x1 + (x2 - x1) * j / (len(wob) - 1), ly + w * self.k]
                c.create_line(*pts, smooth=True, width=max(2, s(2.4)), fill=RED, capstyle="round", tags=T)
            cx, cy = x + CW - P - s(36), y + P + s(8)
            c.create_oval(cx - s(10), cy - s(10), cx + s(10), cy + s(10), fill=GREEN, outline="", tags=T)
            c.create_line(cx - s(5), cy, cx - s(1), cy + s(4), cx + s(5), cy - s(4), fill="#ffffff", width=max(2, s(2.2)),
                          capstyle="round", joinstyle="round", tags=T)
        else:
            c.create_text(x + CW - s(14), y2 - s(12), text="✕", font=self.f_ui, fill=meta, state="hidden",
                          tags=T + ("act_del", f"x{tid}"))
            c.tag_bind(tag, "<Enter>", lambda e, i=tid: self.c.itemconfigure(f"x{i}", state="normal"))
            c.tag_bind(tag, "<Leave>", lambda e, i=tid: self.c.itemconfigure(f"x{i}", state="hidden"))

    # ------------------------------------------------ rich text on cards
    def draw_rich(self, x0, y0, width, text, ink, paper, T):
        """Lays out Slack-style formatted text word by word. Returns (bottom, [(x1, x2, ymid) per line])."""
        c, s = self.c, self.s
        fonts = {"": self.f_card, "b": self.f_card_b, "i": self.f_card_i, "s": self.f_card, "code": self.f_code}
        ls = self.f_card.metrics("linespace")
        space = self.f_card.measure(" ")
        code_bg = self.mix(paper, INK, 0.09)
        extents = []
        y = y0
        for line in (text.split("\n") or [""]):
            lx = x0
            m = LIST_RE.match(line)
            if m:
                level = (len(m.group(1)) + 1) // 3
                mk = m.group(2)
                mark = ("•" if level % 2 == 0 else "◦") if mk in "-*•◦" else mk[:-1] + "."
                mx = x0 + level * s(18)
                c.create_text(mx + s(2), y, anchor="nw", text=mark, font=self.f_card_b if mk[0].isdigit() else self.f_card,
                              fill=ink, tags=T)
                lx = mx + max(s(16), self.f_card_b.measure(mark) + s(7))
                line = line[m.end():].lstrip()
            cx, line_left, line_right = lx, lx, lx
            for chunk, style in runs(line):
                f = fonts[style]
                for tok in re.findall(r"\S+|\s+", chunk):
                    if tok.isspace():
                        if cx > line_left:
                            cx += space * len(tok)
                        continue
                    w = f.measure(tok)
                    if cx + w > x0 + width and cx > line_left:
                        extents.append((line_left, line_right, y + ls * 0.56))
                        y += ls
                        cx = line_right = line_left
                    if style == "code":
                        self.rrect(cx - s(2), y + s(1), cx + w + s(2), y + ls - s(1), s(4), fill=code_bg, outline="", tags=T)
                    it = c.create_text(cx, y + (s(1) if style == "code" else 0), anchor="nw", text=tok, font=f,
                                       fill=self.mix(ink, RED, 0.35) if style == "code" else ink, tags=T)
                    if style == "s":
                        mid = y + ls * 0.55
                        c.create_line(cx, mid, cx + w, mid, fill=ink, width=max(1, s(1.3)), tags=T)
                    cx += w
                    line_right = cx
            extents.append((line_left, max(line_right, line_left + s(4)), y + ls * 0.56))
            y += ls
        return y, extents

    # ------------------------------------------------ images
    def run_ps(self, args, done, script=None, raw=False):
        """Runs the image helper hidden, without blocking the board; calls done(ok)."""
        try:
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
                                     "-File", str(script or CLIP_PS1)] + [str(a) for a in args],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        except OSError:
            return done("" if raw else False)

        def wait():
            if proc.poll() is None:
                self.root.after(120, wait)
            else:
                out = proc.stdout.read().decode("utf-8", errors="ignore").strip()
                done(out if raw else out.endswith("OK"))
        self.root.after(150, wait)

    def preview_args(self, name, src=""):
        s = self.s
        return [IMAGES / f"{name}.png", IMAGES / name, self.CW - 2 * s(16), s(120), s(62), s(9), src or ""]

    def photo(self, name, kind="w"):
        key = (name, kind)
        if key not in self.img_cache:
            f = IMAGES / f"{name}_{kind}.png"
            if not f.exists():
                full = IMAGES / f"{name}.png"
                if full.exists() and name not in self.regen:   # older image: make the new previews once
                    self.regen.add(name)

                    def done(ok, name=name):
                        for k in [k for k in self.img_cache if k[0] == name]:
                            del self.img_cache[k]
                        if not self.editor and not self.press:
                            self.render()
                    self.run_ps(self.preview_args(name, full) + ["thumbs"], done)
                return None
            try:
                self.img_cache[key] = tk.PhotoImage(file=str(f))
            except tk.TclError:
                return None
        return self.img_cache[key]

    def draw_images(self, x0, y0, width, names, paper, T):
        c, s = self.c, self.s
        kind = "w" if len(names) == 1 else "s"
        cx, y, row_h = x0, y0, 0
        for name in names:
            ph = self.photo(name, kind)
            tg = T + ("act_img", f"img:{name}")
            if not ph:   # still being prepared
                w, h = (width, s(120)) if kind == "w" else (s(62), s(62))
                if cx + w > x0 + width and cx > x0:
                    cx, y, row_h = x0, y + row_h + s(8), 0
                self.rrect(cx, y, cx + w, y + h, s(9), fill=self.mix(paper, INK, 0.1), outline="", tags=tg)
            else:
                w, h = ph.width(), ph.height()
                if cx + w > x0 + width and cx > x0:
                    cx, y, row_h = x0, y + row_h + s(8), 0
                c.create_image(cx, y, anchor="nw", image=ph, tags=tg)
            cx += w + s(8)
            row_h = max(row_h, h)
        return y + row_h

    def paste(self, w, target):
        """Ctrl+V: text pastes as usual; an image on the clipboard becomes a small preview on the card."""
        try:
            if self.root.clipboard_get():
                return None
        except tk.TclError:
            pass
        name = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        self.pasting += 1
        self.update_preview()

        def done(ok):
            self.pasting -= 1
            if ok:
                if target == "strip":
                    self.pending_images.append(name)
                else:
                    t = self.find(target)
                    if t is not None:
                        t.setdefault("images", []).append(name)
                        self.save()
                        if not self.editor:
                            self.render()
            self.update_preview()
        self.run_ps(self.preview_args(name) + ["paste"], done)
        return "break"

    # ------------------------------------------------ copy a card
    # ------------------------------------------------ your AI assistant (optional, set in settings.json)
    def assistant(self):
        """settings.json "assistant": {"name": "Instinct", "url": "https://app.instinct.com/"} adds a button for it.
        The board never sends anything by itself: it only opens the page, and copies a card when you ask."""
        a = self.settings.get("assistant") or {}
        url = str(a.get("url") or "")
        if not url.startswith("https://"):
            return None
        return {"name": str(a.get("name") or "Assistant")[:20], "url": url}

    def draw_assistant_pill(self, right):
        a = self.assistant()
        if not a:
            return right + self.s(10)
        c, s = self.c, self.s
        tg = ("ui", "act_assist")
        lh = self.f_ui.metrics("linespace")
        top = s(16)
        tx = c.create_text(right - s(14), top + s(6) + lh / 2, anchor="e", text=f"{a['name']}  \u2197", font=self.f_ui,
                           fill="#c9d6ff", tags=tg)
        b = c.bbox(tx)
        bg = self.rrect(b[0] - s(14), top, right, top + lh + s(12), s(14), fill="#26324f", outline="", tags=tg)
        c.tag_lower(bg, tx)
        return b[0] - s(14)

    def open_assistant(self):
        a = self.assistant()
        if a:
            os.startfile(a["url"])

    def send_to_assistant(self, tid):
        a, t = self.assistant(), self.find(tid)
        if not a or not t:
            return
        text = plain(t["text"]).strip()
        if t.get("time"):
            text = f"{nice_time(t['time'])}: {text}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.copied(tid, True, f"Copied, paste it in {a['name']}")
        os.startfile(a["url"])

    def copy_card(self, tid, what="auto"):
        t = self.find(tid)
        if not t:
            return
        text = t["text"].strip()
        if what == "image" or (what == "auto" and not text and t.get("images")):
            f = IMAGES / f"{t['images'][0]}.png"
            self.run_ps(["", "", 0, 0, 0, 0, f, "copy"], lambda ok: self.copied(tid, ok, "Image copied"))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.copied(tid, True, "Copied")

    def copied(self, tid, ok, msg):
        t = self.find(tid)
        if not t or not ok:
            return
        c, s = self.c, self.s
        c.delete("copied")
        x, y = self.card_xy(t)
        tx = c.create_text(x + self.CW - s(16), y - s(30), anchor="e", text=msg, font=self.f_tab, fill="#ffffff", tags=("ui", "copied"))
        b = c.bbox(tx)
        bg = self.rrect(b[0] - s(10), b[1] - s(5), b[2] + s(10), b[3] + s(5), s(10), fill=GREEN, outline="", tags=("ui", "copied"))
        c.tag_lower(bg, tx)
        self.root.after(1300, lambda: c.delete("copied"))

    def open_image(self, name):
        f = IMAGES / f"{name}.png"
        if f.exists():
            os.startfile(f)

    def remove_image(self, tid, name):
        t = self.find(tid)
        if t and name in t.get("images", []):
            self.snapshot()
            t["images"].remove(name)
            self.save()
            self.render()
            self.note("Image removed.")

    # ------------------------------------------------ lists
    def cur_line(self, w):
        return w.get("insert linestart", "insert lineend")

    def smart_newline(self, w):
        if w is self.entry:
            self.hide_placeholder()
        line = self.cur_line(w)
        m = LIST_RE.match(line)
        if not m:
            w.insert("insert", "\n")
        elif not line[m.end():].strip():          # empty item: end the list
            w.delete("insert linestart", "insert lineend")
        else:
            w.insert("insert", "\n" + m.group(1) + next_marker(m.group(2)) + " ")
        self.after_type(w)
        return "break"

    def indent(self, w, step):
        line = self.cur_line(w)
        m = LIST_RE.match(line)
        if not m:
            return False
        ind, mk = m.group(1), m.group(2)
        new_ind = ind + INDENT if step > 0 else ind[len(INDENT):]
        if step < 0 and not ind:
            return True
        if mk in "-*•◦":
            new_mk = "-"
        else:
            # continue from the previous item at the new level, or start one: 1. -> a. -> 1. ...
            new_mk = None
            row = int(w.index("insert").split(".")[0])
            for r in range(row - 1, 0, -1):
                prev = LIST_RE.match(w.get(f"{r}.0", f"{r}.end"))
                if not prev or len(prev.group(1)) < len(new_ind):
                    break
                if prev.group(1) == new_ind and prev.group(2) not in "-*•◦":
                    new_mk = next_marker(prev.group(2))
                    break
            if not new_mk:
                new_mk = "a." if (len(new_ind) // len(INDENT)) % 2 else "1."
        col = int(w.index("insert").split(".")[1])
        w.delete("insert linestart", f"insert linestart+{m.end()}c")
        w.insert("insert linestart", new_ind + new_mk + " ")
        w.mark_set("insert", f"insert linestart+{max(0, col + len(new_ind) + len(new_mk) - m.end() + 1)}c")
        return True

    def tab_key(self, w, step):
        if w is self.entry and step > 0 and self.current_hash() is not None:
            opts = [g for g in self.known_tags() if g.startswith(self.current_hash()) and g != self.current_hash()]
            if opts:
                self.complete(opts[0])
                return "break"
        if self.indent(w, step):
            self.after_type(w)
        return "break"

    def toggle_list(self, w, kind):
        if w is self.entry:
            self.hide_placeholder()
        line = self.cur_line(w)
        m = LIST_RE.match(line)
        if m:
            w.delete("insert linestart", f"insert linestart+{m.end()}c")
        else:
            w.insert("insert linestart", "1. " if kind == "ol" else "- ")
        self.after_type(w)
        return "break"

    # ------------------------------------------------ formatting inside text boxes (strip and card editor)
    def setup_rich(self, w, big=True):
        w.tag_configure("b", font=self.f_input_b if big else self.f_card_b)
        w.tag_configure("i", font=self.f_input_i if big else self.f_card_i)
        w.tag_configure("s", overstrike=1)
        w.tag_configure("code", font=self.f_input_code if big else self.f_code, background="#e2ddd2", foreground="#8a3a2a")
        w.tag_configure("mk", foreground="#b3ada2")
        w.tag_configure("when", foreground=ACCENT)
        w.tag_raise("sel")
        for seq, ch in (("<Control-b>", "*"), ("<Control-B>", "*"), ("<Control-i>", "_"), ("<Control-I>", "_"),
                        ("<Control-Shift-X>", "~"), ("<Control-Shift-x>", "~"), ("<Control-Shift-C>", "`"), ("<Control-Shift-c>", "`")):
            w.bind(seq, lambda e, ch=ch, w=w: self.wrap_sel(w, ch))
        w.bind("<Shift-Return>", lambda e, w=w: self.smart_newline(w))
        w.bind("<Tab>", lambda e, w=w: self.tab_key(w, 1))
        w.bind("<Shift-Tab>", lambda e, w=w: self.tab_key(w, -1))
        w.bind("<Control-ampersand>", lambda e, w=w: self.toggle_list(w, "ol"))
        w.bind("<Control-asterisk>", lambda e, w=w: self.toggle_list(w, "ul"))
        w.tag_configure("lm", foreground=ACCENT, font=self.f_input_b if big else self.f_card_b)
        w.bind("<Control-BackSpace>", lambda e, w=w: self.delete_word(w, -1))
        w.bind("<Control-Delete>", lambda e, w=w: self.delete_word(w, 1))

    def delete_word(self, w, way):
        """Ctrl+Backspace / Ctrl+Delete: remove the word before / after the cursor (or the selection)."""
        if w is self.entry and self.placeholder_on:
            return "break"
        if w.tag_ranges("sel"):
            w.delete("sel.first", "sel.last")
        elif way < 0:
            before = w.get("insert linestart", "insert")
            m = re.search(r"(\w+|[^\w\s]+)?\s*$", before)
            n = len(before) - m.start() if m and m.start() < len(before) else 1
            w.delete(f"insert -{n}c", "insert")
        else:
            after = w.get("insert", "insert lineend")
            m = re.match(r"\s*(\w+|[^\w\s]+)?", after)
            n = m.end() if m and m.end() else 1
            w.delete("insert", f"insert +{n}c")
        self.after_type(w)
        return "break"

    def wrap_sel(self, w, ch):
        if w is self.entry:
            self.hide_placeholder()
        try:
            a, b = w.index("sel.first"), w.index("sel.last")
        except tk.TclError:
            w.insert("insert", ch + ch)
            w.mark_set("insert", "insert-1c")
            self.after_type(w)
            return "break"
        txt = w.get(a, b)
        new = txt[1:-1] if len(txt) >= 2 and txt[0] == ch and txt[-1] == ch else ch + txt + ch
        w.delete(a, b)
        w.insert(a, new)
        w.tag_add("sel", a, f"{a}+{len(new)}c")
        self.after_type(w)
        return "break"

    def highlight(self, w):
        for tg in ("b", "i", "s", "code", "mk", "when", "lm") + tuple(x for x in w.tag_names() if x.startswith("h_")):
            w.tag_remove(tg, "1.0", "end")
        if w is self.entry and self.placeholder_on:
            return
        text = w.get("1.0", "end-1c")
        at = lambda n: f"1.0+{n}c"
        for m in FMT_RE.finditer(text):
            a, b = m.start(), m.end()
            w.tag_add(STYLE_OF[m.group(0)[0]], at(a + 1), at(b - 1))
            w.tag_add("mk", at(a), at(a + 1))
            w.tag_add("mk", at(b - 1), at(b))
        for m in TAG_RE.finditer(text):
            g = m.group(2).lower()
            name = "h_" + g
            col = self.ink(g)[0] if g in self.settings["tags"] else PALETTE[len(self.settings["tags"]) % len(PALETTE)][0]
            w.tag_configure(name, foreground=col, font=self.f_input_b if w is self.entry else self.f_card_b)
            w.tag_add(name, at(m.start(2) - 1), at(m.end(2)))
        row = 1
        for ln in text.split("\n"):
            lm = LIST_RE.match(ln)
            if lm:
                w.tag_add("lm", f"{row}.{len(lm.group(1))}", f"{row}.{lm.end() - 1}")
            row += 1
        m = re.match(r"^\s*(\d{1,2}(:\d{2})?\s*(am|pm)?)\s", text, re.I)
        if m and parse_task(text)[0]:
            w.tag_add("when", at(m.start(1)), at(m.end(1)))

    def after_type(self, w):
        self.highlight(w)
        if w is self.entry:
            self.grow_strip()
            self.update_preview()

    # ------------------------------------------------ writing strip
    def build_input(self):
        s = self.s
        self.strip = tk.Frame(self.root, bg=PAPER, padx=s(4), pady=s(2))
        self.into = tk.Frame(self.strip, bg=PAPER)
        self.into.pack(side="left", anchor="n", pady=(s(12), 0))
        self.entry = tk.Text(self.strip, font=self.f_input, width=48, height=1, wrap="word", bg=PAPER, fg=INK,
                             insertbackground=INK, relief="flat", bd=0, highlightthickness=0, padx=0, pady=s(10),
                             undo=True, selectbackground="#c9d6f6", selectforeground=INK, inactiveselectbackground="#d9e2f8")
        self.entry.pack(side="left", padx=(0, s(26)))
        self.setup_rich(self.entry)
        self.strip_anchor = (self.W // 2, self.H - s(34))
        self.strip_win = self.c.create_window(0, 0, window=self.strip, anchor="sw", tags="inputwin")
        self.layout_strip()
        self.placeholder_on = False
        self.show_placeholder()
        e = self.entry
        e.bind("<Button-1>", self.focus_entry)
        e.bind("<FocusIn>", lambda ev: (self.hide_placeholder(), self.update_preview()))
        e.bind("<FocusOut>", lambda ev: (self.show_placeholder(), self.update_preview()))
        e.bind("<Return>", self.add_task)
        e.bind("<Control-v>", lambda ev: self.paste(e, "strip"))
        e.bind("<Control-V>", lambda ev: self.paste(e, "strip"))
        e.bind("<Escape>", self.entry_escape)
        e.bind("<KeyRelease>", lambda ev: None if ev.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R") else self.after_type(e))

    def layout_strip(self):
        s = self.s
        self.strip.update_idletasks()
        fw, fh = self.strip.winfo_reqwidth(), self.strip.winfo_reqheight()
        total = fw + s(58)
        x0 = self.strip_anchor[0] - total // 2
        yb = self.strip_anchor[1]
        self.c.coords(self.strip_win, x0 + s(58), yb - s(8))
        self.strip_box = (x0, yb - fh - s(16), x0 + total, yb)

    def grow_strip(self):
        try:
            n = int(self.entry.count("1.0", "end", "displaylines")[0])
        except Exception:
            n = 1
        h = max(1, min(5, n))
        if h != int(self.entry.cget("height")):
            self.entry.config(height=h)
            self.layout_strip()
            self.c.delete("stripbg")
            self.draw_strip_back()
            self.c.tag_raise("inputwin")

    def draw_strip_back(self):
        s = self.s
        x1, y1, x2, y2 = self.strip_box
        r = min((y2 - y1) // 2, s(28))
        T = ("ui", "stripbg")
        self.rrect(x1 + s(2), y1 + s(5), x2 + s(2), y2 + s(5), r, fill="#151514", outline="", tags=T)
        self.rrect(x1, y1, x2, y2, r, fill=PAPER, outline="", tags=T + ("act_strip",))
        cx, cy, rr = x1 + s(30), y2 - s(31), s(15)
        col = self.ink(self.settings["filter"][0])[0] if self.settings["filter"] else ACCENT
        self.c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=col, outline="", tags=T + ("act_strip",))
        self.c.create_line(cx - s(6), cy, cx + s(6), cy, fill="#ffffff", width=max(2, s(2.2)), capstyle="round", tags=T + ("act_strip",))
        self.c.create_line(cx, cy - s(6), cx, cy + s(6), fill="#ffffff", width=max(2, s(2.2)), capstyle="round", tags=T + ("act_strip",))

    def entry_escape(self, _=None):
        if self.entry_text():
            self.entry.delete("1.0", "end")
            self.after_type(self.entry)
        else:
            self.send_back()
        return "break"

    def focus_entry(self, _=None):
        self.root.focus_force()
        self.entry.focus_set()

    def placeholder_text(self):
        return "Write a task for this tag" if self.settings["filter"] else "Write a task.  3pm for a reminder, #tag to group"

    def show_placeholder(self):
        if not self.entry.get("1.0", "end-1c"):
            self.entry.insert("1.0", self.placeholder_text())
            self.entry.config(fg=INK_3)
            self.placeholder_on = True

    def hide_placeholder(self):
        if self.placeholder_on:
            self.entry.delete("1.0", "end")
            self.entry.config(fg=INK)
            self.placeholder_on = False

    def update_input_context(self):
        for w in self.into.winfo_children():
            w.destroy()
        for g in self.settings["filter"]:
            bg, fg = self.ink(g)
            tk.Label(self.into, text=g, font=self.f_tab, bg=bg, fg=fg, padx=self.s(7), pady=self.s(2)).pack(side="left", padx=(0, self.s(6)))
        if self.placeholder_on:
            self.entry.delete("1.0", "end")
            self.entry.insert("1.0", self.placeholder_text())
        self.layout_strip()

    def entry_text(self):
        return "" if self.placeholder_on else self.entry.get("1.0", "end-1c")

    def current_hash(self):
        if self.placeholder_on:
            return None
        before = self.entry.get("1.0", "insert")
        m = re.search(r"(^|\s)#([\w-]*)$", before, re.U)
        return m.group(2).lower() if m else None

    def update_preview(self):
        c, s = self.c, self.s
        c.delete("prev")
        raw = self.entry_text().strip()
        focused = self.root.focus_get() is self.entry
        sx, sy = self.strip_box[0] + s(22), self.strip_box[1] - s(12)
        x = sx
        T = ("ui", "prev")

        def label(txt, col=ON_DESK_2):
            nonlocal x
            it = c.create_text(x, sy, anchor="sw", text=txt, font=self.f_mono_s, fill=col, tags=T)
            x = c.bbox(it)[2] + s(8)

        def tabchip(g, ink, extra=(), sel=False):
            nonlocal x
            w = self.f_tab.measure(g) + s(20)
            if sel:
                self.rrect(x - s(3), sy - s(25), x + w + s(3), sy + s(3), s(14), fill=ON_DESK, outline="", tags=T + extra)
            self.rrect(x, sy - s(22), x + w, sy, s(11), fill=ink[0], outline="", tags=T + extra)
            c.create_text(x + w / 2, sy - s(11), text=g, font=self.f_tab, fill=ink[1], tags=T + extra)
            x += w + s(6)

        q = self.current_hash()
        if q is not None:
            opts = [g for g in self.known_tags() if g.startswith(q) and g != q][:7]
            if opts:
                label("tab picks")
                for i, g in enumerate(opts):
                    tabchip(g, self.ink(g), ("act_sugg", f"tag:{g}"), sel=i == 0)
            elif q:
                label("new tag")
                tabchip(q, PALETTE[len(self.settings["tags"]) % len(PALETTE)])
        elif raw:
            tm, tags, _ = parse_task(raw)
            tags = list(dict.fromkeys(self.settings["filter"] + tags))
            if tm:
                label("reminder " + nice_time(tm), ON_DESK)
            for g in tags:
                tabchip(g, self.ink(g) if g in self.settings["tags"] else PALETTE[len(self.settings["tags"]) % len(PALETTE)])

        # images waiting to go on the next card
        for name in self.pending_images:
            ph = self.photo(name, "s")
            if ph:
                tg = T + ("act_pimg", f"img:{name}")
                c.create_image(x, sy, anchor="sw", image=ph, tags=tg)
                bx2 = x + ph.width()
                self.rrect(bx2 - s(9), sy - ph.height() - s(5), bx2 + s(5), sy - ph.height() + s(9), s(7), fill=INK, outline="", tags=tg)
                c.create_text(bx2 - s(2), sy - ph.height() + s(2), text="✕", font=self.f_mono_s, fill="#ffffff", tags=tg)
                x = bx2 + s(12)
        if self.pasting:
            label("adding image…")

        # formatting buttons, only while writing
        if focused:
            bx = self.strip_box[2] - s(22)
            for key, lab, font in (("ul", "•", tkfont.Font(family="Segoe UI", size=13, weight="bold")), ("ol", "1.", self.f_tab), ("code", "</>", self.f_mono_s),
                                   ("s", "S", tkfont.Font(family="Segoe UI", size=10, overstrike=1)),
                                   ("i", "I", tkfont.Font(family="Georgia", size=10, slant="italic")),
                                   ("b", "B", tkfont.Font(family="Segoe UI", size=10, weight="bold"))):
                w = s(30)
                tg = T + ("act_fmt", f"fmt:{key}")
                self.rrect(bx - w, sy - s(26), bx, sy, s(9), fill=DESK_HI, outline="", tags=tg)
                c.create_text(bx - w / 2, sy - s(13), text=lab, font=font, fill=ON_DESK, tags=tg)
                bx -= w + s(5) + (s(10) if key == "code" else 0)

    def complete(self, g):
        e = self.entry
        before = e.get("1.0", "insert")
        new_before = re.sub(r"#([\w-]*)$", "#" + g + " ", before)
        after = re.sub(r"^\S*\s?", "", e.get("insert", "end-1c"))
        e.delete("1.0", "end")
        e.insert("1.0", new_before + after)
        e.mark_set("insert", f"1.0+{len(new_before)}c")
        self.after_type(e)

    def on_tab(self, _):
        q = self.current_hash()
        if q is not None:
            opts = [g for g in self.known_tags() if g.startswith(q) and g != q]
            if opts:
                self.complete(opts[0])
        return "break"

    def add_task(self, _=None):
        raw = self.entry_text().strip()
        if not raw and not self.pending_images:
            return "break"
        tm, tags, text = parse_task(raw) if raw else (None, [], "")
        tags = list(dict.fromkeys(self.settings["filter"] + tags))
        for g in tags:
            self.ink(g)
        self.tasks.append({"id": self.next_id(), "text": text, "time": tm, "tags": tags, "done": False,
                           "fx": None, "fy": None, "since": self.date, "created": now_hm(), "images": self.pending_images})
        self.pending_images = []
        self.entry.delete("1.0", "end")
        self.entry.config(height=1)
        self.layout_strip()
        self.save()
        self.render()
        return "break"

    # ------------------------------------------------ mouse
    def current(self):
        tags = self.c.gettags("current")
        cid = next((int(t[1:]) for t in tags if re.fullmatch(r"c\d+", t)), None)
        tg = next((t[4:] for t in tags if t.startswith("tag:")), None)
        return tags, cid, tg

    def on_press(self, e):
        self.close_popup()
        tags, cid, tg = self.current()
        if "act_hide" in tags:
            return self.hide()
        if "act_clear" in tags:
            return self.clear_menu()
        if "act_mem" in tags:
            return self.mem_panel()
        if "act_assist" in tags:
            return self.open_assistant()
        if "act_all" in tags:
            return self.set_filter([])
        if "act_filter" in tags and tg:
            return self.toggle_filter(tg)
        if "act_sugg" in tags and tg:
            self.focus_entry()
            return self.complete(tg)
        if "act_undo" in tags:
            return self.undo()
        if "act_strip" in tags:
            return self.focus_entry()
        if "act_fmt" in tags:
            key = next(t[4:] for t in tags if t.startswith("fmt:"))
            self.focus_entry()
            if key in ("ol", "ul"):
                return self.toggle_list(self.entry, key)
            return self.wrap_sel(self.entry, {"b": "*", "i": "_", "s": "~", "code": "`"}[key])
        if "act_pimg" in tags:
            name = next(t[4:] for t in tags if t.startswith("img:"))
            self.pending_images = [n for n in self.pending_images if n != name]
            return self.update_preview()
        if cid is not None:
            if "act_tab" in tags and tg:
                return self.toggle_filter(tg)
            if "act_time" in tags:
                return self.time_popup(cid, e.x_root, e.y_root + self.s(14))
            if "act_del" in tags:
                return self.delete(cid)
            if "act_img" in tags:
                return self.open_image(next(t[4:] for t in tags if t.startswith("img:")))
            if "act_copy" in tags:
                return self.copy_card(cid)
            self.press = {"id": cid, "x": e.x, "y": e.y, "lx": e.x, "ly": e.y, "moved": False}
            self.c.tag_raise(f"c{cid}")
            self.c.tag_raise("inputwin")
            return
        # empty desk: take keyboard so typing goes to the strip
        self.root.focus_force()
        self.c.focus_set()

    def on_motion(self, e):
        p = self.press
        if not p:
            return
        if not p["moved"] and abs(e.x - p["x"]) + abs(e.y - p["y"]) > 4:
            p["moved"] = True
        if p["moved"]:
            self.c.move(f"c{p['id']}", e.x - p["lx"], e.y - p["ly"])
            p["lx"], p["ly"] = e.x, e.y

    def on_release(self, e):
        p, self.press = self.press, None
        if not p:
            return
        t = self.find(p["id"])
        if not t:
            return
        if p["moved"]:
            x, y = self.card_xy(t)
            x = max(self.s(10), min(x + e.x - p["x"], self.W - self.CW - self.s(10)))
            y = max(self.top_safe, min(y + e.y - p["y"], self.H - self.s(210)))
            t["fx"], t["fy"] = x / self.W, y / self.H
            self.tasks.remove(t)
            self.tasks.append(t)  # stays on top
            self.save()
            self.render()
        else:
            if self.click_job:
                self.root.after_cancel(self.click_job)
            self.click_job = self.root.after(240, lambda i=t["id"]: self.toggle(i))

    def on_double(self, e):
        if self.click_job:
            self.root.after_cancel(self.click_job)
            self.click_job = None
        tags, cid, _ = self.current()
        if cid is not None and not any(a in tags for a in ("act_tab", "act_time", "act_del", "act_img", "act_copy")):
            self.edit(cid)

    def on_canvas_key(self, e):
        if e.char and e.char.isprintable() and not (e.state & 0x4):
            self.focus_entry()
            self.hide_placeholder()
            self.entry.insert("end-1c", e.char)
            self.after_type(self.entry)

    # ------------------------------------------------ actions
    def toggle(self, tid):
        self.click_job = None
        t = self.find(tid)
        if t:
            t["done"] = not t.get("done")
            if t["done"]:
                self.bump_streak()
            self.save()
            self.render()

    def delete(self, tid):
        self.snapshot()
        self.tasks = [t for t in self.tasks if t["id"] != tid]
        self.save()
        self.render()
        self.note("Card deleted.")

    def toggle_tag(self, tid, g):
        t = self.find(tid)
        if t:
            tags = t.setdefault("tags", [])
            if g in tags:
                tags.remove(g)
            else:
                tags.append(g)
            self.save()
            self.render()

    def edit(self, tid, add_tag=False):
        t = self.find(tid)
        if not t:
            return
        self.close_editor()
        s = self.s
        x, y = self.card_xy(t)
        raw = (nice_time(t["time"]).replace(" ", "") + " " if t.get("time") else "") + t["text"] + \
            "".join(" #" + g for g in t.get("tags", [])) + (" #" if add_tag else "")
        lines = max(2, min(7, sum(len(ln) // 26 + 1 for ln in raw.split("\n"))))
        ent = tk.Text(self.root, font=self.f_card, width=10, height=lines, wrap="word", bg="#ffffff", fg=INK,
                      insertbackground=INK, relief="flat", bd=0, padx=s(8), pady=s(6), undo=True,
                      highlightthickness=s(2), highlightcolor=ACCENT, highlightbackground=ACCENT,
                      selectbackground="#c9d6f6", selectforeground=INK)
        self.setup_rich(ent, big=False)
        ent.insert("1.0", raw)
        self.highlight(ent)
        if add_tag:
            ent.mark_set("insert", "end-1c")
        else:
            ent.tag_add("sel", "1.0", "end-1c")
        self.c.create_window(x + s(6), y + s(28), window=ent, anchor="nw", width=self.CW - s(12), tags=("ui", "editor"))
        self.editor = ent

        def commit(_=None):
            if self.editor is not ent:
                return "break"
            val = ent.get("1.0", "end-1c").strip()
            self.close_editor()
            if val:
                tm, tags, text = parse_task(val)
                for g in tags:
                    self.ink(g)
                t.update(time=tm, tags=tags, text=text)
                self.save()
            self.render()
            return "break"

        ent.bind("<Return>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>", lambda e: (self.close_editor(), self.render(), "break")[-1])
        ent.bind("<KeyRelease>", lambda e: self.highlight(ent))
        ent.bind("<Control-v>", lambda e: self.paste(ent, tid))
        ent.bind("<Control-V>", lambda e: self.paste(ent, tid))
        self.root.focus_force()
        ent.focus_set()

    def close_editor(self):
        if self.editor:
            ent, self.editor = self.editor, None
            self.c.delete("editor")
            ent.destroy()

    def tidy(self):
        key = lambda t: (t.get("done", False), (t.get("tags") or ["~"])[0], t.get("time") or "99", t["id"])
        self.tasks.sort(key=key)
        for t in self.tasks:
            t["fx"] = t["fy"] = None
        self.render()
        self.save()

    def clear_done(self):
        n = sum(1 for t in self.tasks if t.get("done"))
        if n:
            self.snapshot()
            self.tasks = [t for t in self.tasks if not t.get("done")]
            self.save()
            self.render()
            self.note(f"Cleared {n} finished card{'s' if n > 1 else ''}.")

    def clear_all(self):
        n = len(self.tasks)
        if n:
            self.snapshot()
            self.tasks = []
            self.save()
            self.render()
            self.note(f"Board cleared, {n} card{'s' if n > 1 else ''} removed.")

    def clear_menu(self):
        done = sum(1 for t in self.tasks if t.get("done"))
        m = self.menu()
        m.add_command(label=f"Clear scratched-off cards ({done})" if done else "Clear scratched-off cards",
                      command=self.clear_done, state="normal" if done else "disabled")
        m.add_separator()
        m.add_command(label=f"Clear the whole board ({len(self.tasks)})", foreground="#ff8a73", command=self.clear_all)
        x, y = self.clear_at
        m.tk_popup(self.root.winfo_rootx() + int(x), self.root.winfo_rooty() + int(y))

    def quit(self):
        self.save()
        self.root.destroy()

    # ------------------------------------------------ menus
    def menu(self):
        return tk.Menu(self.root, tearoff=0, bg=DESK_HI, fg=ON_DESK, activebackground=RULE, activeforeground=ON_DESK,
                       bd=0, font=self.f_ui)

    def on_right(self, e, card_menu=False):
        self.close_popup()
        tags, cid, tg = self.current()
        if cid is not None and not card_menu:   # right-click a card = delete it (Undo note appears)
            return self.delete(cid)
        m = self.menu()
        if cid is not None and "act_img" in tags:
            name = next(t[4:] for t in tags if t.startswith("img:"))
            m.add_command(label="Open image", command=lambda: self.open_image(name))
            m.add_separator()
            m.add_command(label="Remove image", foreground="#ff8a73", command=lambda: self.remove_image(cid, name))
        elif cid is not None:
            t = self.find(cid)
            m.add_command(label="Bring it back" if t.get("done") else "Scratch it off", command=lambda: self.toggle(cid))
            m.add_command(label="Edit", command=lambda: self.edit(cid))
            if t["text"].strip():
                m.add_command(label="Copy text", command=lambda: self.copy_card(cid, "text"))
                if self.assistant():
                    m.add_command(label=f"Ask {self.assistant()['name']} about this",
                                  command=lambda: self.send_to_assistant(cid))
            if t.get("images"):
                m.add_command(label="Copy image", command=lambda: self.copy_card(cid, "image"))
            m.add_command(label="Change reminder" if t.get("time") else "Set a reminder",
                          command=lambda: self.time_popup(cid, e.x_root, e.y_root))
            m.add_separator()
            for g in self.known_tags()[:10]:
                m.add_command(label=("✓  " if g in t.get("tags", []) else "     ") + g, command=lambda g=g: self.toggle_tag(cid, g))
            m.add_command(label="     New tag…", command=lambda: self.edit(cid, add_tag=True))
            m.add_separator()
            m.add_command(label="Delete", foreground="#ff8a73", command=lambda: self.delete(cid))
        elif "act_filter" in tags and tg:
            m.add_command(label=f"Show only {tg}", command=lambda: self.set_filter([tg]))
            m.add_command(label="Change colour", command=lambda: self.recolour(tg))
            m.add_separator()
            m.add_command(label=f"Remove {tg} from every card", foreground="#ff8a73", command=lambda: self.drop_tag(tg))
        else:
            if self.assistant():
                m.add_command(label=f"Open {self.assistant()['name']}", command=self.open_assistant)
            m.add_command(label="Tidy up, grouped by tag", command=self.tidy)
            m.add_command(label="Clear scratched-off cards", command=self.clear_done)
            m.add_command(label="Clear the whole board", foreground="#ff8a73", command=self.clear_all)
            m.add_separator()
            m.add_command(label="Shortcuts and formatting", command=lambda: self.show_help(e.x_root, e.y_root))
            m.add_command(label="Open the boards folder", command=lambda: os.startfile(BOARDS))
            m.add_command(label="Hide Desk Board (Alt + ` brings it back)", command=self.hide)
        m.tk_popup(e.x_root, e.y_root)

    def recolour(self, g):
        self.settings["tags"][g] = (self.settings["tags"][g] + 1) % len(PALETTE)
        self.save()
        self.render()

    def drop_tag(self, g):
        self.snapshot()
        for t in self.tasks:
            t["tags"] = [x for x in t.get("tags", []) if x != g]
        self.settings["tags"].pop(g, None)
        self.save()
        self.render()
        self.note(f"Removed the {g} tag.")

    # ------------------------------------------------ memory: pill on the board + panel to close heavy apps
    def mem_colours(self, pct):
        if pct >= 90:
            return RED, "#ffffff", RED
        if pct >= 75:
            return "#4a3a14", "#f2b33d", "#f2b33d"
        return DESK_HI, ON_DESK_2, GREEN

    def draw_mem_pill(self, right):
        c, s = self.c, self.s
        w = self.f_ui.measure("memory 100%") + s(40)
        left = right - w
        tg = ("ui", "act_mem")
        lh = self.f_ui.metrics("linespace")
        top = s(22) - s(6)
        self.rrect(left, top, right, top + lh + s(12), s(14), fill=DESK_HI, outline="", tags=tg + ("mem_bg",))
        cy = top + s(6) + lh / 2
        c.create_oval(left + s(14), cy - s(4), left + s(22), cy + s(4), fill=GREEN, outline="", tags=tg + ("mem_dot",))
        c.create_text(left + s(30), cy, anchor="w", text="memory", font=self.f_ui, fill=ON_DESK_2, tags=tg + ("mem_txt",))
        self.update_mem_pill()
        return left

    def update_mem_pill(self):
        pct = procs.memory_status()[0]
        bg, fg, dot = self.mem_colours(pct)
        self.c.itemconfigure("mem_bg", fill=bg)
        self.c.itemconfigure("mem_dot", fill=dot if pct < 90 else "#ffffff")
        self.c.itemconfigure("mem_txt", text=f"memory {pct}%", fill=fg)

    def mem_panel(self):
        self.close_popup()
        s = self.s
        p, cv, _ = self.rounded_window(DESK_HI)
        self.popup = self.mem_popup = p
        self.mem_state, self.tabs, self.tabs_busy, self.tabs_gone = {}, [], False, set()
        self.mem_open = getattr(self, "mem_open", set())       # browsers whose tabs are shown
        self.mem_all = set()                                   # browsers showing every tab
        cv.bind("<Button-1>", lambda e: self.mem_click(p, cv))
        p.bind("<Escape>", lambda e: (self.close_popup(), self.send_back()))
        p.bind("<MouseWheel>", lambda e: self.mem_scroll(cv, e))
        self.draw_mem(p, cv)
        x0, y0, x1, y1 = self.c.bbox("mem_bg")
        pw = int(cv["width"])
        p.geometry(f"+{int(self.left + min(x1, self.W - s(16)) - pw)}+{int(self.top + y1 + s(10))}")
        self.mem_top = y1 + s(10)
        p.focus_force()
        p.bind("<FocusOut>", lambda e: self.root.after(80, lambda: self.popup is p and p.focus_get() is None and self.close_popup()))
        self.load_tabs(p, cv)

        def tick(n=1):
            if self.popup is p:
                self.draw_mem(p, cv)
                self.update_mem_pill()
                if n % 2 == 0:
                    self.load_tabs(p, cv)
                self.root.after(2000, lambda: tick(n + 1))
        self.root.after(2000, tick)

    def load_tabs(self, p, cv):
        """Reads the open browser tabs in the background (about half a second)."""
        if self.tabs_busy:
            return
        self.tabs_busy = True

        def done(out):
            self.tabs_busy = False
            try:
                rows = json.loads(out or "[]")
            except ValueError:
                rows = []
            tabs = []
            for r in rows if isinstance(rows, list) else []:
                name = r.get("name") or ""
                m = TAB_MEM_RE.search(name)
                size = 0
                if m:
                    size = float(m.group(1).replace(",", "")) * 1024 ** ("KMG".index(m.group(2).upper()) + 1)
                title = TAB_MEM_RE.sub("", name).strip() or name
                tabs.append({"pid": r.get("pid"), "hwnd": r.get("hwnd"), "name": name, "title": title, "bytes": size})
            self.tabs = tabs
            if tabs and not getattr(self, "mem_open_seen", False):   # first time: show the tabs of every browser
                self.mem_open_seen = True
                self.mem_open |= {k for k, ap in getattr(self, "mem_apps", {}).items()
                                  if any(t["pid"] in ap["pids"] for t in tabs)}
            if self.popup is p:
                self.draw_mem(p, cv)
        self.run_ps(["list"], done, script=TABS_PS1, raw=True)

    def draw_mem(self, p, cv):
        s = self.s
        pct, used, total = procs.memory_status()
        apps = procs.top_apps(10, skip={os.getpid()})
        now = dt.datetime.now().timestamp()
        for key, stt in list(self.mem_state.items()):   # follow apps we asked to close
            if not procs.alive(stt["pids"]):
                del self.mem_state[key]
            elif stt["stage"] == "closing" and now - stt["t"] > 3:
                stt["stage"] = "force"
        self.mem_apps = {a["key"]: a for a in apps}
        self.tab_index = {}

        PW, pad = s(500), s(22)
        max_h = self.H - getattr(self, "mem_top", s(70)) - s(16)
        cv.delete("all")
        bar = self.mem_colours(pct)[2]
        y = pad
        cv.create_text(pad, y, anchor="nw", text="Memory", font=self.f_card_b, fill=ON_DESK)
        cv.create_text(PW - pad, y + s(3), anchor="ne", text=f"{pct}% used  ·  {procs.nice_size(used)} of {procs.nice_size(total)}",
                       font=self.f_mono_s, fill=ON_DESK_2)
        y += s(32)
        self.rrect_on(cv, pad, y, PW - pad, y + s(8), s(4), fill=RULE)
        self.rrect_on(cv, pad, y, pad + max(s(8), (PW - 2 * pad) * pct / 100), y + s(8), s(4), fill=bar)
        y += s(26)
        cv.create_text(pad, y, anchor="nw", text="APPS USING THE MOST", font=self.f_mono_s, fill=ON_DESK_3)
        y += s(24)
        top = max([a["bytes"] for a in apps] or [1])
        for a in apps:
            tabs = [t for t in self.tabs if t["pid"] in a["pids"] and (t["hwnd"], t["name"]) not in self.tabs_gone]
            stage = self.mem_state.get(a["key"], {}).get("stage")
            cv.create_text(pad, y, anchor="nw", text=a["name"], font=self.f_ui, fill=ON_DESK)
            n = len(a["pids"])
            if n > 1:
                cv.create_text(pad + self.f_ui.measure(a["name"]) + s(8), y + s(2), anchor="nw", text=f"{n} processes",
                               font=self.f_mono_s, fill=ON_DESK_3)
            barw = PW - 2 * pad - s(270)
            self.rrect_on(cv, pad, y + s(24), pad + max(s(4), barw * a["bytes"] / top), y + s(28), s(2),
                          fill="#5d7fd9" if a["bytes"] < 1024 ** 3 else FLAME)
            label, bfill, bfg = {"closing": ("Closing…", DESK, ON_DESK_3), "force": ("Force quit", RED, "#ffffff"),
                                 "admin": ("Needs admin", DESK, ON_DESK_3)}.get(stage, ("End", RULE, ON_DESK))
            tg = ("btn", f"app:{a['key']}")
            self.rrect_on(cv, PW - pad - s(84), y + s(1), PW - pad, y + s(27), s(13), fill=bfill, tags=tg)
            cv.create_text(PW - pad - s(42), y + s(14), text=label, font=self.f_mono_s, fill=bfg, tags=tg)
            size_x = PW - pad - s(96)
            if tabs:
                is_open = a["key"] in self.mem_open
                tg = ("btn", f"tabs:{a['key']}")
                tx1 = PW - pad - s(92)
                self.rrect_on(cv, tx1 - s(82), y + s(1), tx1, y + s(27), s(13), fill=ACCENT if is_open else DESK, tags=tg)
                cv.create_text(tx1 - s(41), y + s(14), text=f"{len(tabs)} tabs {'▴' if is_open else '▾'}", font=self.f_mono_s,
                               fill="#ffffff" if is_open else ON_DESK_2, tags=tg)
                size_x = tx1 - s(92)
            cv.create_text(size_x, y + s(13), anchor="e", text=procs.nice_size(a["bytes"]), font=self.f_mono, fill=ON_DESK_2)
            y += s(42)

            if tabs and a["key"] in self.mem_open:
                tabs = sorted(tabs, key=lambda t: -t["bytes"])
                show = tabs if a["key"] in self.mem_all else tabs[:6]
                for t in show:
                    key = f"{t['hwnd']}:{len(self.tab_index)}"
                    self.tab_index[key] = t
                    self.rrect_on(cv, pad + s(6), y + s(2), PW - pad, y + s(26), s(8), fill="#33332f")
                    title_w = PW - 2 * pad - s(150)
                    title = t["title"]
                    while self.f_ui.measure(title) > title_w and len(title) > 4:
                        title = title[:-2]
                    if title != t["title"]:
                        title = title.rstrip() + "…"
                    cv.create_text(pad + s(18), y + s(14), anchor="w", text=title, font=self.f_ui, fill=ON_DESK)
                    if t["bytes"]:
                        cv.create_text(PW - pad - s(44), y + s(14), anchor="e", text=procs.nice_size(t["bytes"]),
                                       font=self.f_mono_s, fill=FLAME if t["bytes"] >= 500 * 1024 ** 2 else ON_DESK_2)
                    tg = ("btn", f"tab:{key}")
                    cx, cy = PW - pad - s(18), y + s(14)
                    cv.create_oval(cx - s(10), cy - s(10), cx + s(10), cy + s(10), fill=RULE, outline="", tags=tg)
                    cv.create_text(cx, cy, text="✕", font=self.f_mono_s, fill=ON_DESK, tags=tg)
                    y += s(30)
                if len(show) < len(tabs):
                    tg = ("btn", f"alltabs:{a['key']}")
                    cv.create_text(pad + s(18), y + s(4), anchor="nw", text=f"show all {len(tabs)} tabs",
                                   font=self.f_mono_s, fill="#8fa8ff", tags=tg)
                    y += s(26)
                y += s(8)
        if self.tabs_busy and not self.tabs:
            cv.create_text(pad, y, anchor="nw", text="reading browser tabs…", font=self.f_mono_s, fill=ON_DESK_3)
            y += s(20)
        y += s(4)
        cv.create_line(pad, y, PW - pad, y, fill=RULE)
        y += s(12)
        cv.create_text(pad, y, anchor="nw", text="End closes the app like its \u2715, so it can still ask to save.",
                       font=self.f_mono_s, fill=ON_DESK_3)
        cv.create_text(PW - pad, y + s(22), anchor="ne", text="Windows Task Manager \u2197", font=self.f_mono_s, fill="#8fa8ff",
                       tags=("btn", "taskmgr"))
        H = y + s(22) + s(16) + pad
        view_h = int(min(H, max_h))                       # taller than the screen: scroll with the mouse wheel
        cv.config(width=PW, height=view_h, scrollregion=(0, 0, PW, H), yscrollincrement=s(30))
        bgp = cv.create_polygon(self.rr_pts(0, 0, PW, view_h, s(18)), smooth=True, splinesteps=12, fill=DESK_HI,
                                outline="", tags="panelbg")
        cv.tag_lower(bgp)
        self.mem_panel_bg(cv)
        p.geometry(f"{PW}x{view_h}")

    def mem_panel_bg(self, cv):
        """Keeps the rounded background behind the visible part while scrolling."""
        top, vh, pw = cv.canvasy(0), int(cv["height"]), int(cv["width"])
        cv.coords("panelbg", *self.rr_pts(0, top, pw, top + vh, self.s(18)))

    def mem_scroll(self, cv, e):
        cv.yview_scroll(-1 if e.delta > 0 else 1, "units")
        self.mem_panel_bg(cv)

    def rrect_on(self, cv, x0, y0, x1, y1, r, fill, tags=()):
        return cv.create_polygon(self.rr_pts(x0, y0, x1, y1, r), smooth=True, splinesteps=8, fill=fill, outline="", tags=tags)

    def mem_click(self, p, cv):
        tags = cv.gettags("current")
        if "taskmgr" in tags:
            self.close_popup()
            return os.startfile("taskmgr.exe")
        tab = next((t[4:] for t in tags if t.startswith("tab:")), None)
        if tab is not None and tab in self.tab_index:
            t = self.tab_index[tab]
            self.tabs_gone.add((t["hwnd"], t["name"]))          # hide it right away
            import base64
            self.run_ps(["close", t["hwnd"], base64.b64encode(t["name"].encode("utf-8")).decode()],
                        lambda out: self.popup is p and self.load_tabs(p, cv), script=TABS_PS1, raw=True)
            return self.draw_mem(p, cv)
        for prefix, bucket in (("tabs:", self.mem_open), ("alltabs:", self.mem_all)):
            k = next((t[len(prefix):] for t in tags if t.startswith(prefix)), None)
            if k is not None:
                bucket.symmetric_difference_update({k})
                return self.draw_mem(p, cv)
        key = next((t[4:] for t in tags if t.startswith("app:")), None)
        if key is None:
            return
        a = self.mem_apps.get(key)
        stt = self.mem_state.get(key)
        if stt and stt["stage"] == "force":
            if procs.force_end(procs.alive(stt["pids"])):
                stt["stage"] = "admin"
        elif a and not stt:
            if procs.close_app(a["pids"]):
                self.mem_state[key] = {"stage": "closing", "t": dt.datetime.now().timestamp(), "pids": list(a["pids"])}
            elif procs.force_end(a["pids"]):     # no window to close (a background app): end it
                self.mem_state[key] = {"stage": "admin", "t": 0, "pids": list(a["pids"])}
        self.draw_mem(p, cv)
        self.root.after(400, lambda: self.popup is p and self.draw_mem(p, cv))

    # ------------------------------------------------ "2 of 5 done" pill above the taskbar clock
    def build_badge(self):
        """A small pill that floats over every window, bottom-right. Click it: the board comes up; click again: it goes back."""
        w = tk.Toplevel(self.root, bg=KEY)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        cv = tk.Canvas(w, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        cv.pack(fill="both", expand=True)
        cv.bind("<ButtonRelease-1>", lambda e: self.badge_click())
        w.update_idletasks()
        h = user32.GetParent(w.winfo_id()) or w.winfo_id()
        _SetLong(h, -20, (_GetLong(h, -20) | 0x80 | 0x08000000) & ~0x40000)  # no taskbar button, never takes focus
        self.badge, self.badge_cv, self.badge_hwnd = w, cv, h
        self.badge_key, self.badge_shown = None, True

    def update_badge(self):
        if not getattr(self, "badge", None):
            return
        s, cv = self.s, self.badge_cv
        n = len(self.tasks)
        done = sum(1 for t in self.tasks if t.get("done"))
        if n:
            txt, dot = f"{done} of {n} done", (GREEN if done == n else FLAME)
        else:
            txt, dot = "Desk Board", ON_DESK_2
        if self.badge_key == (txt, dot):
            return
        self.badge_key = (txt, dot)
        cv.delete("all")
        f = self.f_ui
        pad, d = s(14), s(8)
        bw, bh = pad + d + s(8) + f.measure(txt) + pad, f.metrics("linespace") + s(14)
        cv.config(width=bw, height=bh)
        self.rrect_on(cv, 0, 0, bw, bh, bh // 2, fill=RULE)
        self.rrect_on(cv, 1, 1, bw - 1, bh - 1, bh // 2 - 1, fill=DESK_HI)
        cv.create_oval(pad, (bh - d) / 2, pad + d, (bh + d) / 2, fill=dot, outline="")
        cv.create_text(pad + d + s(8), bh / 2, anchor="w", text=txt, font=f, fill=ON_DESK)
        x, y = self.left + self.W - bw - s(14), self.top + self.H - bh - s(10)
        self.badge.geometry(f"{bw}x{bh}+{x}+{y}")

    def badge_click(self):
        if self.front:
            self.close_popup()
            self.send_back()
        else:
            self.bring_front()

    def badge_tick(self):
        """Keep the pill on top, but step aside while a full-screen app (video, game, slides) is in front."""
        fg = user32.GetForegroundWindow()
        full = False
        if fg and fg not in (self.hwnd, self.badge_hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(fg, ctypes.byref(r))
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(fg, cls, 64)
            full = (r.left <= 0 and r.top <= 0 and r.right >= user32.GetSystemMetrics(0)
                    and r.bottom >= user32.GetSystemMetrics(1) and cls.value not in ("Progman", "WorkerW"))
        if full and self.badge_shown:
            self.badge.withdraw()
            self.badge_shown = False
        elif not full:
            if not self.badge_shown:
                self.badge.deiconify()
                self.badge_shown = True
            user32.SetWindowPos(self.badge_hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    # ------------------------------------------------ rounded pop-up windows
    def rounded_window(self, bg):
        w = tk.Toplevel(self.root, bg=KEY)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        cv = tk.Canvas(w, bg=KEY, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        body = tk.Frame(cv, bg=bg)
        return w, cv, body

    def finish_rounded(self, cv, body, bg, pad, r):
        body.update_idletasks()
        bw, bh = body.winfo_reqwidth() + 2 * pad, body.winfo_reqheight() + 2 * pad
        cv.config(width=bw, height=bh)
        cv.delete("bg")
        cv.create_polygon(self.rr_pts(0, 0, bw, bh, r), smooth=True, splinesteps=12, fill=bg, outline="", tags="bg")
        if not cv.find_withtag("body"):
            cv.create_window(pad, pad, window=body, anchor="nw", tags="body")
        return bw, bh

    def show_help(self, x, y):
        self.close_popup()
        s = self.s
        p, cv, box = self.rounded_window(PAPER)
        self.popup = p
        tk.Label(box, text="Shortcuts", font=self.f_card_b, fg=INK, bg=PAPER).pack(anchor="w", pady=(0, s(8)))
        grid = tk.Frame(box, bg=PAPER)
        grid.pack(anchor="w")
        hk = self.settings.get("hotkeys") or DEFAULT_HOTKEYS
        rows = [(hk.get("board", HOTKEY_TEXT), "bring the board up / send it back"),
                (hk.get("memory", DEFAULT_HOTKEYS["memory"]), "open the memory panel"), ("Enter", "add the card"), ("Shift + Enter", "new line"),
                ("3pm  14:30", "at the start sets a reminder"), ("#tag", "groups cards, Tab picks one"),
                ("Ctrl + B", "*bold*"), ("Ctrl + I", "_italic_"), ("Ctrl + Shift + X", "~strike~"), ("Ctrl + Shift + C", "`code`"),
                ("1.  or  - ", "numbered or bullet list"), ("Tab / Shift+Tab", "indent a list item (1. → a.)"),
                ("Ctrl + V", "paste an image as a small preview"), ("click", "scratch off"), ("double-click", "edit"), ("right-click a card", "delete it (Undo appears)"), ("Shift + right-click", "card options")]
        for i, (k, v) in enumerate(rows):
            tk.Label(grid, text=k, font=self.f_mono_s, fg=ACCENT, bg=PAPER).grid(row=i, column=0, sticky="w", padx=(0, s(14)), pady=s(1))
            tk.Label(grid, text=v, font=self.f_ui, fg=INK, bg=PAPER).grid(row=i, column=1, sticky="w", pady=s(1))
        bw, bh = self.finish_rounded(cv, box, PAPER, s(18), s(18))
        p.geometry(f"+{int(min(x, self.left + self.W - bw - s(8)))}+{int(min(y, self.top + self.H - bh - s(8)))}")
        p.focus_force()
        p.bind("<FocusOut>", lambda e: self.root.after(80, lambda: self.popup is p and p.focus_get() is None and self.close_popup()))
        p.bind("<Button-1>", lambda e: self.close_popup())

    # ------------------------------------------------ reminder picker
    def close_popup(self):
        if self.popup:
            p, self.popup = self.popup, None
            p.destroy()

    def time_popup(self, tid, x, y):
        t = self.find(tid)
        if not t:
            return
        self.close_popup()
        s = self.s
        p, cv, box = self.rounded_window(DESK_HI)
        self.popup = p

        def set_time(v):
            t["time"] = v
            self.close_popup()
            self.save()
            self.render()

        tk.Label(box, text=("Reminder at " + nice_time(t["time"])) if t.get("time") else "Remind me at",
                 font=self.f_mono_s, fg=ON_DESK_3, bg=DESK_HI).pack(anchor="w", pady=(0, s(6)))
        row = tk.Frame(box, bg=DESK_HI)
        row.pack(fill="x")
        default = t.get("time") or from_min((to_min(now_hm()) + 30 + 14) // 15 * 15)
        ent = tk.Entry(row, font=("Cascadia Mono", 12), width=9, bg=PAPER, fg=INK, insertbackground=INK, relief="flat",
                       highlightthickness=0)
        ent.insert(0, nice_time(default))
        ent.select_range(0, "end")
        ent.pack(side="left", ipady=s(5), padx=(0, s(6)))
        msg = tk.Label(box, text="", font=self.f_mono_s, fg="#ff8a73", bg=DESK_HI)

        def apply(_=None):
            v = parse_clock(ent.get())
            if v:
                set_time(v)
            else:
                msg.config(text="Try 3pm, 3:30 pm or 15:30")
                msg.pack(anchor="w", pady=(s(6), 0))
                self.finish_rounded(cv, box, DESK_HI, s(14), s(14))

        btn = dict(font=self.f_ui, relief="flat", bd=0, cursor="hand2", padx=s(12), pady=s(4))
        tk.Button(row, text="Set", bg=ACCENT, fg="#ffffff", activebackground="#4a78e8", activeforeground="#ffffff", command=apply, **btn).pack(side="left")
        quick = tk.Frame(box, bg=DESK_HI)
        quick.pack(fill="x", pady=(s(8), 0))
        for lbl, n in (("+15m", 15), ("+30m", 30), ("+1h", 60), ("+2h", 120)):
            tk.Button(quick, text=lbl, bg=RULE, fg=ON_DESK_2, activebackground="#4a4944", activeforeground=ON_DESK,
                      command=lambda n=n: set_time(from_min(to_min(now_hm()) + n)),
                      **dict(btn, padx=s(8), font=self.f_mono_s)).pack(side="left", padx=(0, s(4)))
        if t.get("time"):
            tk.Button(box, text="Remove reminder", bg=DESK_HI, fg="#ff8a73", activebackground=RULE, activeforeground="#ff8a73",
                      command=lambda: set_time(None), **dict(btn, padx=0)).pack(anchor="w", pady=(s(8), 0))
        ent.bind("<Return>", apply)
        ent.bind("<Escape>", lambda e: self.close_popup())
        bw, bh = self.finish_rounded(cv, box, DESK_HI, s(14), s(14))
        px = min(x, self.left + self.W - bw - s(8))
        py = min(y, self.top + self.H - bh - s(8))
        p.geometry(f"+{int(px)}+{int(py)}")
        p.focus_force()
        ent.focus_set()

        def maybe_close(_):
            self.root.after(80, lambda: self.popup is p and p.focus_get() is None and self.close_popup())
        p.bind("<FocusOut>", maybe_close)

    # ------------------------------------------------ notes and reminders
    def note(self, text):
        """Small paper note at the bottom right with Undo, gone after 6 s."""
        c, s = self.c, self.s
        c.delete("note")
        T = ("ui", "note")
        x2, y2 = self.W - s(30), self.H - s(110)
        tx = c.create_text(x2 - s(70), y2 - s(20), anchor="e", text=text, font=self.f_ui, fill=INK, tags=T)
        ux = c.create_text(x2 - s(16), y2 - s(20), anchor="e", text="Undo", font=tkfont.Font(family="Bahnschrift SemiBold", size=10),
                           fill=RED, tags=T + ("act_undo",))
        b = c.bbox(tx)
        bg = c.create_rectangle(b[0] - s(16), y2 - s(40), x2, y2, fill=PAPER, outline="", tags=T)
        c.tag_lower(bg, tx)
        c.tag_raise(ux)
        self.root.after(6000, lambda: c.delete("note"))

    def check_reminders(self):
        now = to_min(now_hm())
        for t in self.tasks:
            if t.get("done") or not t.get("time") or t.get("since", self.date) < self.date:
                continue
            key = f"{t['id']}@{t['time']}"
            diff = now - to_min(t["time"])
            if 0 <= diff < 10 and key not in self.reminded:
                self.reminded.add(key)
                self.reminder_popup(t)

    def reminder_popup(self, t):
        """A paper note above all windows, bottom-right, until you answer it."""
        s = self.s
        paper, meta = self.card_colour(t)
        accent = self.ink(t["tags"][0])[0] if t.get("tags") else ACCENT
        w, cv, box = self.rounded_window(paper)
        tk.Label(box, text="Reminder  ·  " + nice_time(t["time"]), font=self.f_mono_s, fg=meta, bg=paper).pack(anchor="w")
        tk.Label(box, text=plain(t["text"]), font=self.f_card, fg=INK, bg=paper, wraplength=s(290), justify="left").pack(anchor="w", pady=(s(4), s(12)))
        row = tk.Frame(box, bg=paper)
        row.pack(anchor="e")

        def close():
            if w in self.reminder_wins:
                self.reminder_wins.remove(w)
            w.destroy()
            self.stack_reminders()

        def snooze():
            t["time"] = from_min(to_min(now_hm()) + 15)
            self.save()
            self.render()
            close()

        def done():
            t["done"] = True
            self.bump_streak()
            self.save()
            self.render()
            close()

        btn = dict(font=self.f_ui, relief="flat", bd=0, cursor="hand2", padx=s(12), pady=s(5))
        tk.Button(row, text="Snooze 15 min", bg=self.mix(paper, INK, 0.1), fg=INK, activebackground=self.mix(paper, INK, 0.18),
                  command=snooze, **btn).pack(side="left", padx=(0, s(6)))
        tk.Button(row, text="Done  ✓", bg=accent, fg="#ffffff", activebackground=self.mix(accent, INK, 0.2),
                  activeforeground="#ffffff", command=done, **btn).pack(side="left")
        bw, bh = self.finish_rounded(cv, box, paper, s(18), s(18))
        w._wh = (max(bw, s(320)), bh)
        self.reminder_wins.append(w)
        self.stack_reminders()
        try:
            winsound.MessageBeep(0x40)
        except Exception:
            pass

    def stack_reminders(self):
        s = self.s
        y = self.top + self.H - s(64)          # above the "2 of 5 done" pill
        for w in reversed(self.reminder_wins):
            wd, h = w._wh
            y -= h
            w.geometry(f"{wd}x{h}+{self.left + self.W - wd - s(24)}+{y}")
            y -= s(10)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    DeskBoard().run()
