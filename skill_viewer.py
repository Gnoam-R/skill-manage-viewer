"""Skill catalog panel: lists the skills each agent (Claude Code / Codex / Antigravity) can use, grouped by
category, and installs / updates / edits / deletes them. Lives in an always-on-top corner window that is
opened and closed from a tray icon.

  pythonw skill_viewer.py            start (window visible; closing it hides it to the tray)
  pythonw skill_viewer.py --hidden   start in the tray only
  python  skill_viewer.py --check    print counts per agent/category, no GUI
  python  skill_viewer.py --make-icon  rewrite skill_viewer.ico

Needs: pip install pystray pillow     (the file operations themselves live in skill_manager.py)
"""
import contextlib
import ctypes
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import messagebox, simpledialog

import skill_manager as mgr

HOME = Path.home()
NOTES = json.loads(Path(__file__).with_name("skill_notes.json").read_text(encoding="utf-8"))

# First match wins; the skill name is tried against every rule before its description is. Edit freely.
CATEGORIES = [
    ("코드 리뷰·보안", r"review|audit|critique|security|simplify"),
    ("Git·사내 규칙", r"gitlab|commit|\bpr\b|git|worktree|branch"),
    ("기획·검증", r"grill|brainstorm|plan|\bspec\b|interview"),
    ("설계·아키텍처", r"architecture|codebase-design|domain-model"),
    ("개발·테스트", r"tdd|test|debug|diagnos|verification|ponytail|lsp|pyright|reverse-engineer"),
    ("UI·디자인", r"design|\bui\b|ux|brand|banner|slides|theme|frontend|impeccable|figma|canvas|styling|polish|dataviz"
                 r"|visualiz|generative"),
    ("문서·파일", r"docx|pdf|pptx|xlsx|\bdocs?\b|google-workspace|openai-docs"),
    ("브라우저·자동화", r"browser|chrome|computer-use|playwright|schedule|loop|automation|mcp|workflow|agent|imagegen"),
    ("스킬·설정 관리", r"skill|plugin|config|setup|keybind|init|customiz|claude-api|migrate|permission|morning|memory|antigravity"),
    ("리서치", r"research|context7|explain"),
]
OTHER = "기타"
AGENTS = list(mgr.AGENT_DIRS)

LIGHT = dict(bg="#F3F5F8", surface="#FFFFFF", line="#DFE3E8", text="#18212C", muted="#5E6A78",
             accent="#1D5FD0", on_accent="#FFFFFF", soft="#EAF1FD", warn_bg="#FCF1DC", warn_fg="#8A5A00")
DARK = dict(bg="#12171D", surface="#1A2129", line="#2B343E", text="#E6EAEE", muted="#93A0AE",
            accent="#6FA3F5", on_accent="#0B1220", soft="#223247", warn_bg="#3A2D14", warn_fg="#F0C77A")
FONT = "Malgun Gothic"
DEFAULT_HINT = "클릭: 이름 복사 · ⋯: 수정·삭제 · 트레이 아이콘으로 열기/닫기"


# ---- data -------------------------------------------------------------------------------------
def categorize(name: str, desc: str) -> str:
    for text in (name.lower(), desc.lower()):
        for cat, rx in CATEGORIES:
            if re.search(rx, text):
                return cat
    return OTHER


def brief(name: str, desc: str, user_notes: dict[str, str] | None = None) -> str:
    """Korean one-liner of what a skill is for: the user's own note, then the bundled one, then the
    first sentence of its description."""
    for src in (user_notes or {}, NOTES):
        if n := src.get(name) or src.get(name.split(":")[-1]):
            return n
    first = re.split(r"(?<=[.!?。])\s", desc, maxsplit=1)[0]
    return first if len(first) <= 80 else first[:77] + "…"


def scan(root: Path, prefix: str = "") -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirs, files in os.walk(root):
        if "SKILL.md" in files:
            s = mgr.parse(Path(dirpath) / "SKILL.md")
            s["name"] = prefix + s["name"]
            s["path"] = Path(dirpath)
            out.append(s)
            dirs.clear()  # don't descend into a skill's own folders
    return out


def claude_plugin_roots() -> list[tuple[Path, str]]:
    try:
        installed = json.loads((HOME / ".claude/plugins/installed_plugins.json").read_text(encoding="utf-8"))["plugins"]
        enabled = json.loads((HOME / ".claude/settings.json").read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, KeyError, ValueError):
        return []
    return [(Path(v[0]["installPath"]) / "skills", key.split("@")[0] + ":")
            for key, v in installed.items() if enabled.get(key) and v]


def describe_origin(s: dict, kind: str, root: Path) -> None:
    """Fills s['origin'] (short label), s['repo'] (git clone it follows) and s['managed'] (may be edited/deleted here)."""
    p = s["path"]
    s["repo"], s["managed"] = None, False
    if kind == "plugin":
        ver = root.parent.name
        s["origin"] = "플러그인" if re.fullmatch(r"[0-9a-f]{12}", ver) else f"플러그인 v{ver}"
    elif kind == "builtin":
        s["origin"] = "내장"
    elif ".system" in p.relative_to(root).parts:
        s["origin"] = "시스템"
    elif p.parent != root:
        s["origin"] = "앱 동기화"
    else:
        s["managed"] = True
        how, repo = mgr.origin(p)
        s["repo"] = repo
        s["origin"] = {"git": "git · " + (mgr.version(repo).split(" · ")[0] if repo else ""),
                       "own": "내 스킬", "link": "링크", "local": "로컬"}[how]


def collect(agent: str) -> list[dict]:
    roots = [(mgr.AGENT_DIRS[agent], "", "user")]
    if agent == "Claude Code":
        roots += [(r, prefix, "plugin") for r, prefix in claude_plugin_roots()]
    elif agent == "Antigravity":
        roots.append((HOME / ".gemini/antigravity-cli/builtin/skills", "", "builtin"))
    notes = mgr.load_notes()
    seen: dict[str, dict] = {}
    for root, prefix, kind in roots:
        for s in scan(root, prefix):
            if s["name"] not in seen:
                describe_origin(s, kind, root)
                seen[s["name"]] = s | {"agent": agent}
    skills = sorted(seen.values(), key=lambda s: s["name"].lower())
    for s in skills:
        s["cat"] = categorize(s["name"], s["desc"])
        s["note"] = brief(s["name"], s["desc"], notes)
    return skills


def check() -> None:
    for a in AGENTS:
        skills = collect(a)
        assert skills, f"no skills found for {a}"
        assert all(s["name"] and s["cat"] and s["note"] and s["origin"] for s in skills)
        counts = Counter(s["cat"] for s in skills)
        print(f"{a}: {len(skills)}", dict(counts))
        print("   origin:", dict(Counter(s["origin"].split(" ·")[0] for s in skills)))
        print("   기타:", [s["name"] for s in skills if s["cat"] == OTHER])


# ---- windows helpers ---------------------------------------------------------------------------
def work_area() -> tuple[int, int, int, int]:
    """Screen area minus the taskbar, in pixels."""
    class RECT(ctypes.Structure):
        _fields_ = [(n, ctypes.c_long) for n in ("left", "top", "right", "bottom")]
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def system_is_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def icon_image(size: int = 256):
    """App icon: a 2x2 catalog grid on a blue tile, one cell highlighted. Drawn at 4x and downsampled."""
    from PIL import Image, ImageDraw
    s = 1024
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((32, 32, s - 32, s - 32), radius=232, fill="#1D5FD0")
    cell, gap, x0 = 252, 68, 220
    colors = ["#FFFFFF", "#C7DAFB", "#C7DAFB", "#4DE0B5"]
    for i, color in enumerate(colors):
        x, y = x0 + (i % 2) * (cell + gap), x0 + (i // 2) * (cell + gap)
        d.rounded_rectangle((x, y, x + cell, y + cell), radius=64, fill=color)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def make_ico(path: Path) -> None:
    icon_image(256).save(path, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])


class Scroll:
    """A vertically scrollable frame: pack content into .body, place .wrap."""

    def __init__(self, parent, c: dict):
        self.wrap = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"])
        self.canvas = tk.Canvas(self.wrap, bg=c["surface"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.scroller = self  # type: ignore[attr-defined]  # lets the wheel handler find us
        self.body = tk.Frame(self.canvas, bg=c["surface"])
        win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))

    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()


class App:
    W, H = 400, 640

    def __init__(self, with_tray: bool = True):
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        self.root = root = tk.Tk()
        root.title("스킬 카탈로그")
        from PIL import ImageTk
        self._icon = ImageTk.PhotoImage(icon_image(64))  # keep a reference or Tk drops it
        root.iconphoto(True, self._icon)  # pyright: ignore[reportArgumentType]
        root.attributes("-topmost", True, "-toolwindow", True)  # toolwindow: no taskbar button
        root.resizable(False, True)
        self.scale = root.winfo_fpixels("1i") / 96
        self.c = DARK if system_is_dark() else LIGHT
        root.configure(bg=self.c["bg"])
        self.corner = "tr"
        self.visible = True
        self.collapsed: set[str] = set()
        self.updatable: set[Path] = set()
        self.data: dict[str, list[dict]] = {}
        self.agent = tk.StringVar(value=AGENTS[0])
        self.query = tk.StringVar()
        self.events: queue.Queue = queue.Queue()
        self.tray = None
        self.dialog: AddDialog | None = None
        self._say_job = None
        self._build()
        self.query.trace_add("write", lambda *_: self.render(top=True))
        root.protocol("WM_DELETE_WINDOW", self.hide if with_tray else self.quit)
        root.bind_all("<MouseWheel>", self._wheel)
        self.load()
        self.place()
        self._dark_titlebar()
        if with_tray:
            self.start_tray()
        root.after(100, self._pump)
        root.after(60_000, self._tick)

    # ---- widgets ----------------------------------------------------------------------------
    def px(self, n: int) -> int:
        return int(n * self.scale)

    def _label(self, parent, text="", size=9, bold=False, fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                        fg=fg or self.c["text"], bg=bg or self.c["surface"], **kw)

    def _icon_button(self, parent, text, command, hint=""):
        b = self._label(parent, text, 11, fg=self.c["muted"], cursor="hand2", padx=8, pady=2)
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: (b.config(bg=self.c["soft"], fg=self.c["accent"]), self.footer.config(text=hint)))
        b.bind("<Leave>", lambda e: (b.config(bg=self.c["surface"], fg=self.c["muted"]),
                                     self.footer.config(text=DEFAULT_HINT, fg=self.c["muted"])))
        return b

    def _button(self, parent, text, command, primary=False):
        c = self.c
        return tk.Button(parent, text=text, command=command, font=(FONT, 9, "bold" if primary else "normal"),
                         relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                         bg=c["accent"] if primary else c["surface"], fg=c["on_accent"] if primary else c["text"],
                         activebackground=c["accent"] if primary else c["soft"],
                         activeforeground=c["on_accent"] if primary else c["accent"],
                         highlightthickness=0 if primary else 1, highlightbackground=c["line"],
                         disabledforeground=c["muted"])

    def _check(self, parent, text, var, bg=None, **kw):
        c = self.c
        bg = bg or c["bg"]
        return tk.Checkbutton(parent, text=text, variable=var, font=(FONT, 9), fg=c["text"], bg=bg,
                              activebackground=bg, activeforeground=c["text"], selectcolor=c["surface"],
                              anchor="w", bd=0, highlightthickness=0, cursor="hand2", **kw)

    def _entry(self, parent, var):
        c = self.c
        box = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        e = tk.Entry(box, textvariable=var, relief="flat", bg=c["surface"], fg=c["text"],
                     insertbackground=c["text"], font=(FONT, 9))
        e.pack(fill="x", ipady=6, padx=8)
        return box, e

    def _tab_bar(self, parent, names, on_pick):
        """Underlined tabs. -> (frame, restyle(selected, texts=None))."""
        c = self.c
        bar = tk.Frame(parent, bg=c["surface"])
        cells = {}
        for n in names:
            cell = tk.Frame(bar, bg=c["surface"], cursor="hand2")
            cell.pack(side="left", expand=True, fill="x")
            lab = self._label(cell, n, 9, pady=8)
            lab.pack(fill="x")
            underline = tk.Frame(cell, height=2, bg=c["surface"])
            underline.pack(fill="x")
            for w in (cell, lab):
                w.bind("<Button-1>", lambda e, n=n: on_pick(n))
            cells[n] = (lab, underline)

        def restyle(selected, texts=None):
            for n, (lab, underline) in cells.items():
                on = n == selected
                lab.config(text=(texts or {}).get(n, n), fg=c["accent"] if on else c["muted"],
                           font=(FONT, 9, "bold" if on else "normal"))
                underline.config(bg=c["accent"] if on else c["surface"])
        return bar, restyle

    def _build(self) -> None:
        c = self.c
        header = tk.Frame(self.root, bg=c["surface"])
        header.pack(fill="x")
        titles = tk.Frame(header, bg=c["surface"])
        titles.pack(side="left", padx=(16, 0), pady=(12, 10))
        self._label(titles, "스킬 카탈로그", 11, bold=True).pack(anchor="w")
        self.subtitle = self._label(titles, "", 8, fg=c["muted"])
        self.subtitle.pack(anchor="w")
        actions = tk.Frame(header, bg=c["surface"])
        actions.pack(side="right", padx=(0, 6))
        # created before self.footer exists; the hint handlers only touch it when the pointer is over a button
        for text, cmd, hint in (("＋", self.add_dialog, "스킬 추가 (링크로 설치 · 새로 만들기)"),
                                ("⇧", lambda: self.check_updates(), "git으로 받은 스킬의 업데이트 확인"),
                                ("⟳", self.load, "목록 새로고침"),
                                ("⇄", self.move, "창 위치 전환 (우상단 ↔ 좌하단)"),
                                ("–", self.hide, "트레이로 숨기기")):
            self._icon_button(actions, text, cmd, hint).pack(side="left")

        self.tabs, self._restyle_tabs = self._tab_bar(self.root, AGENTS, self.set_agent)
        self.tabs.pack(fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(fill="x")

        search = tk.Frame(self.root, bg=c["bg"])
        search.pack(fill="x", padx=12, pady=(10, 4))
        box = tk.Frame(search, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        box.pack(fill="x")
        self._label(box, "검색", 8, fg=c["muted"], padx=8).pack(side="left")
        self.entry = tk.Entry(box, textvariable=self.query, relief="flat", bg=c["surface"], fg=c["text"],
                              insertbackground=c["text"], font=(FONT, 9))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))

        meta = tk.Frame(self.root, bg=c["bg"])
        meta.pack(fill="x", padx=14, pady=(2, 4))
        self.count = self._label(meta, "", 8, fg=c["muted"], bg=c["bg"])
        self.count.pack(side="left")
        self.fold = self._label(meta, "", 8, fg=c["accent"], bg=c["bg"], cursor="hand2")
        self.fold.pack(side="right")
        self.fold.bind("<Button-1>", lambda e: self.toggle_all())

        self.footer = self._label(self.root, DEFAULT_HINT, 8, fg=c["muted"], bg=c["bg"], anchor="w", padx=14, pady=6)
        self.footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        self.list = Scroll(self.root, c)
        self.list.wrap.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.canvas, self.body = self.list.canvas, self.list.body

    def _wheel(self, e) -> None:
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except KeyError:  # pointer is over a popup menu
            return
        scroller = None
        while w is not None and scroller is None:
            scroller, w = getattr(w, "scroller", None), w.master
        if scroller and scroller.canvas.yview() != (0.0, 1.0):
            scroller.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _dark_titlebar(self, win=None) -> None:
        if self.c is DARK:
            try:
                hwnd = ctypes.windll.user32.GetParent((win or self.root).winfo_id())
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
            except (AttributeError, OSError):
                pass

    def say(self, text: str, warn: bool = False, ms: int = 4000) -> None:
        """Footer message that falls back to the default hint."""
        if self._say_job:
            self.root.after_cancel(self._say_job)
        self.footer.config(text=text, fg=self.c["warn_fg"] if warn else self.c["accent"])
        self._say_job = self.root.after(ms, lambda: self.footer.config(text=DEFAULT_HINT, fg=self.c["muted"]))

    def bg(self, fn, done) -> None:
        """Run fn on a worker thread, then done(result or exception) on the Tk thread."""
        def run():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - surfaced to the user by `done`
                res = e
            self.events.put(lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    # ---- list -------------------------------------------------------------------------------
    def load(self) -> None:
        mgr.version.cache_clear()
        self.data = {a: collect(a) for a in AGENTS}
        self.render()

    def set_agent(self, name: str) -> None:
        self.agent.set(name)
        self.render(top=True)

    def toggle_all(self) -> None:
        cats = {s["cat"] for s in self.data[self.agent.get()]}
        self.collapsed = set() if self.collapsed >= cats else cats
        self.render()

    def render(self, top: bool = False) -> None:
        c, agent = self.c, self.agent.get()
        q = self.query.get().strip().lower()
        skills = [s for s in self.data.get(agent, [])
                  if q in " ".join((s["name"], s["note"], s["desc"], s["origin"])).lower()]
        self.list.clear()
        self._restyle_tabs(agent, {a: f"{a}  {len(self.data.get(a, []))}" for a in AGENTS})
        self.subtitle.config(text=f"{agent}에서 사용할 수 있는 스킬")
        self.count.config(text=f"{len(skills)}개 스킬" + (f" · '{q}' 검색 결과" if q else ""))
        cats = {s["cat"] for s in self.data.get(agent, [])}
        self.fold.config(text="" if q else ("모두 펼치기" if self.collapsed >= cats else "모두 접기"))
        for cat in [n for n, _ in CATEGORIES] + [OTHER]:
            items = [s for s in skills if s["cat"] == cat]
            if not items:
                continue
            folded = cat in self.collapsed and not q
            self._section(cat, len(items), folded)
            if not folded:
                for s in items:
                    self._row(s)
        if not skills:
            self._label(self.body, "일치하는 스킬이 없습니다", 9, fg=c["muted"], pady=30).pack(fill="x")
        if top:
            self.canvas.yview_moveto(0)

    def _section(self, cat: str, n: int, folded: bool) -> None:
        c = self.c
        f = tk.Frame(self.body, bg=c["bg"], cursor="hand2")
        f.pack(fill="x")
        chevron = self._label(f, "▸" if folded else "▾", 8, fg=c["muted"], bg=c["bg"], padx=10)
        chevron.pack(side="left", pady=6)
        title = self._label(f, cat, 9, bold=True, bg=c["bg"])
        title.pack(side="left")
        self._label(f, str(n), 8, fg=c["muted"], bg=c["bg"], padx=8).pack(side="left")
        for w in (f, chevron, title):
            w.bind("<Button-1>", lambda e, cat=cat: self._fold(cat))
        tk.Frame(self.body, height=1, bg=c["line"]).pack(fill="x")

    def _fold(self, cat: str) -> None:
        self.collapsed ^= {cat}
        self.render()

    def _row(self, s: dict) -> None:
        c = self.c
        f = tk.Frame(self.body, bg=c["surface"], cursor="hand2")
        f.pack(fill="x")
        top = tk.Frame(f, bg=c["surface"])
        top.pack(fill="x", padx=(16, 8), pady=(7, 0))
        more = self._label(top, "⋯", 11, fg=c["muted"], padx=6)  # right-hand items are packed first so a long name can't push them out
        more.pack(side="right")
        tag = self._label(top, s["origin"], 7, fg=c["muted"])
        tag.pack(side="right")
        name = self._label(top, s["name"], 9, bold=True, anchor="w", justify="left", wraplength=self.px(self.W - 210))
        name.pack(side="left")
        if s["manual"]:
            self._label(top, "수동", 7, fg=c["warn_fg"], bg=c["warn_bg"], padx=5).pack(side="left", padx=6)
        if s["repo"] in self.updatable:
            self._label(top, "업데이트 있음", 7, fg=c["on_accent"], bg=c["accent"], padx=5).pack(side="left", padx=2)
        note = self._label(f, s["note"], 8, fg=c["muted"], anchor="w", justify="left",
                           wraplength=self.px(self.W - 64))
        note.pack(fill="x", padx=(16, 12), pady=(1, 7))
        tk.Frame(self.body, height=1, bg=c["line"]).pack(fill="x", padx=(16, 0))
        parts = (f, top, name, tag, more, note)

        def tint(color):
            for w in parts:
                w.config(bg=color)
        for w in (*parts, *top.winfo_children()):
            w.bind("<Enter>", lambda e: tint(c["soft"]))
            w.bind("<Leave>", lambda e: tint(c["surface"]))
            w.bind("<Button-1>", lambda e, n=s["name"]: self.copy(n))
            w.bind("<Button-3>", lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        more.bind("<Button-1>", lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        more.config(cursor="hand2")

    def copy(self, name: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(name)
        self.say(f"복사됨: {name}", ms=2000)

    # ---- row actions ------------------------------------------------------------------------
    def build_menu(self, s: dict) -> tk.Menu:
        c = self.c
        m = tk.Menu(self.root, tearoff=0, bg=c["surface"], fg=c["text"], activebackground=c["soft"],
                    activeforeground=c["accent"], bd=0, font=(FONT, 9))
        m.add_command(label="이름 복사", command=lambda: self.copy(s["name"]))
        m.add_command(label="용도 설명 수정…", command=lambda: self.edit_note(s))
        m.add_command(label="SKILL.md 열어서 수정" if s["managed"] else "SKILL.md 열기 (보기)",
                      command=lambda: self.edit_file(s))
        if s["managed"]:
            m.add_separator()
            if s["repo"]:
                m.add_command(label="이 스킬의 저장소 업데이트 확인", command=lambda: self.check_updates([s["repo"]]))
            m.add_command(label="삭제…", command=lambda: self.delete(s))
        else:
            m.add_separator()
            m.add_command(label=f"{s['origin']} 제공 스킬이라 여기서 삭제할 수 없습니다", state="disabled")
        return m

    def row_menu(self, s: dict, x: int, y: int) -> None:
        m = self.build_menu(s)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def edit_note(self, s: dict) -> None:
        cur = brief(s["name"], s["desc"], mgr.load_notes())
        text = simpledialog.askstring("용도 설명 수정", f"{s['name']}\n한 줄 설명 (비우면 기본 설명으로 되돌림)",
                                      initialvalue=cur, parent=self.root)
        if text is None:
            return
        mgr.set_note(s["name"], text)
        self.load()

    def edit_file(self, s: dict) -> None:
        if s["repo"] and not messagebox.askokcancel(
                "원본 저장소 파일 수정",
                f"이 스킬은 git 저장소({mgr.repo_name(s['repo'])})를 그대로 가리킵니다.\n"
                "파일을 고치면 원본 저장소의 파일이 바뀌고, 이후 업데이트 때 충돌할 수 있습니다.\n계속할까요?",
                parent=self.root):
            return
        mgr.open_in_editor(s["path"] / "SKILL.md")

    def delete(self, s: dict) -> None:
        agent, p = s["agent"], s["path"]
        if mgr.is_link(p):
            body = "연결만 해제됩니다. 원본 파일과 다른 에이전트의 같은 스킬은 그대로입니다."
        else:
            body = f"폴더가 휴지통 폴더로 옮겨집니다(복구 가능):\n{mgr.STORE / 'trash'}"
        if not messagebox.askyesno("스킬 삭제", f"'{s['name']}'을(를) {agent}에서 삭제할까요?\n\n{body}", parent=self.root):
            return
        try:
            moved = mgr.remove(agent, p.name)
        except mgr.ManagerError as e:
            messagebox.showerror("삭제 실패", str(e), parent=self.root)
            return
        self.say(f"삭제됨: {s['name']}" + (f" (백업: {moved.name})" if moved else ""))
        self.load()

    # ---- updates ----------------------------------------------------------------------------
    def check_updates(self, repos: list[Path] | None = None) -> None:
        if repos is None:
            repos = sorted({s["repo"] for skills in self.data.values() for s in skills if s["repo"]}, key=str)
        if not repos:
            self.say("업데이트를 확인할 git 스킬이 없습니다")
            return
        self.say(f"업데이트 확인 중… ({len(repos)}개 저장소)", ms=60_000)

        def work():
            found, errors = [], []
            for r in repos:
                try:
                    if mgr.has_update(r):
                        found.append(r)
                except mgr.ManagerError as e:
                    errors.append(f"{mgr.repo_name(r)}: {e}")
            return found, errors
        self.bg(work, self._offer_updates)

    def _offer_updates(self, res) -> None:
        if isinstance(res, Exception):
            self.say(f"업데이트 확인 실패: {res}", warn=True)
            return
        found, errors = res
        self.updatable = set(found)
        self.render()
        if errors:
            messagebox.showwarning("일부 저장소를 확인하지 못했습니다", "\n".join(errors), parent=self.root)
        if not found:
            self.say("모두 최신입니다" if not errors else "확인된 저장소는 모두 최신입니다")
            return
        users = Counter(s["repo"] for skills in self.data.values() for s in skills if s["repo"] in found)
        lines = "\n".join(f"· {mgr.repo_name(r)}  (스킬 {users[r]}개 연결)" for r in found)
        if messagebox.askyesno("업데이트", f"새 버전이 있는 저장소 {len(found)}개:\n{lines}\n\n지금 업데이트할까요?",
                               parent=self.root):
            self.say("업데이트 중…", ms=60_000)
            self.bg(lambda: [(r, mgr.update(r)) for r in found], self._updated)

    def _updated(self, res) -> None:
        if isinstance(res, Exception):
            messagebox.showerror("업데이트 실패", f"{res}\n\n저장소 파일을 직접 고친 경우 충돌일 수 있습니다.", parent=self.root)
            self.say("업데이트 실패", warn=True)
            return
        lines, gone = [], []
        for r, out in res:
            lines.append(f"· {mgr.repo_name(r)}: {out['old']} → {out['new']}")
            gone += out["gone"]
        self.updatable -= {r for r, _ in res}
        if gone:
            lines.append("\n저장소에서 사라져 연결이 끊긴 스킬: " + ", ".join(gone))
        messagebox.showinfo("업데이트 완료", "\n".join(lines), parent=self.root)
        self.load()

    # ---- window / tray ----------------------------------------------------------------------
    def add_dialog(self) -> None:
        if not self.visible:
            self.show()
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.win.lift()
            return
        self.dialog = AddDialog(self)

    def place(self) -> None:
        left, top, right, bottom = work_area()
        w, h = self.px(self.W), min(self.px(self.H), bottom - top - 24)
        x = right - w - 12 if self.corner == "tr" else left + 12
        y = top + 12 if self.corner == "tr" else bottom - h - 12
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def move(self) -> None:
        self.corner = "bl" if self.corner == "tr" else "tr"
        self.place()

    def show(self) -> None:
        self.place()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.visible = True
        self.load()

    def hide(self) -> None:
        self.root.withdraw()
        self.visible = False

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def quit(self) -> None:
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def start_tray(self) -> None:
        import pystray
        put = self.events.put  # tray callbacks run on pystray's thread; the Tk thread drains the queue
        menu = pystray.Menu(
            pystray.MenuItem("열기 / 닫기", lambda i, m: put("toggle"), default=True),
            pystray.MenuItem("스킬 추가…", lambda i, m: put("add_dialog")),
            pystray.MenuItem("업데이트 확인", lambda i, m: put("check_updates")),
            pystray.MenuItem("위치 전환 (우상단 ↔ 좌하단)", lambda i, m: put("move")),
            pystray.MenuItem("새로고침", lambda i, m: put("load")),
            pystray.MenuItem("종료", lambda i, m: put("quit")),
        )
        self.tray = pystray.Icon("skill_viewer", icon_image(64), "스킬 카탈로그", menu)
        self.tray.run_detached()

    def _pump(self) -> None:
        while not self.events.empty():
            item = self.events.get_nowait()
            item() if callable(item) else getattr(self, item)()
        self.root.after(100, self._pump)

    def _tick(self) -> None:
        if self.visible and not self.query.get():  # don't rebuild under the user's search
            self.load()
        self.root.after(60_000, self._tick)

    def run(self, hidden: bool = False) -> None:
        if hidden:
            self.hide()
        self.root.mainloop()


class AddDialog:
    """Install skills from a link, or create a new one. Both tabs link into the agents you tick."""
    W, H = 460, 640

    def __init__(self, app: App):
        self.app, c = app, app.c
        self.win = win = tk.Toplevel(app.root)
        win.title("스킬 추가")
        win.configure(bg=c["bg"])
        win.transient(app.root)
        win.attributes("-topmost", True)
        w, h = app.px(self.W), min(app.px(self.H), win.winfo_screenheight() - 80)
        rx, ry = app.root.winfo_x(), app.root.winfo_y()
        x = rx - w - 12 if rx > w + 24 else rx + app.root.winfo_width() + 12  # beside the panel, not over it
        win.geometry(f"{w}x{h}+{x}+{ry}")
        app._dark_titlebar(win)
        self.mode = "link"
        self.picks: dict[Path, tk.BooleanVar] = {}
        self.agents = {a: tk.BooleanVar(value=True) for a in AGENTS}
        bar, self._restyle = app._tab_bar(win, ["링크로 설치", "새로 만들기"], self.show)
        bar.pack(fill="x")
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        self.panel = tk.Frame(win, bg=c["bg"])
        self.panel.pack(fill="both", expand=True, padx=12, pady=10)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.show("링크로 설치")

    def close(self) -> None:
        self.win.destroy()

    def say(self, text: str, warn: bool = False) -> None:
        c = self.app.c
        self.status.config(text=text, fg=c["warn_fg"] if warn else c["muted"])

    def _agent_checks(self) -> None:
        app, c = self.app, self.app.c
        row = tk.Frame(self.panel, bg=c["bg"])
        row.pack(side="bottom", fill="x", pady=(6, 0))
        app._label(row, "설치할 에이전트", 8, fg=c["muted"], bg=c["bg"]).pack(anchor="w")
        for a, var in self.agents.items():
            app._check(row, a, var).pack(side="left", padx=(0, 10))

    def show(self, tab: str) -> None:
        for w in self.panel.winfo_children():
            w.destroy()
        self._restyle(tab)
        self.mode = tab
        (self._build_link if tab == "링크로 설치" else self._build_new)()

    # -- tab 1: install from link
    def _build_link(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        self.link = tk.StringVar()
        row = tk.Frame(p, bg=c["bg"])
        row.pack(fill="x")
        box, e = app._entry(row, self.link)
        box.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.load_link())
        e.focus_set()
        app._button(row, "불러오기", self.load_link, primary=True).pack(side="left", padx=(8, 0))
        app._label(p, "GitHub 저장소·폴더·SKILL.md 링크 또는 owner/repo[/경로]", 8, fg=c["muted"], bg=c["bg"],
                   anchor="w").pack(fill="x", pady=(4, 0))
        self.status = app._label(p, "", 8, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 40))
        self.status.pack(fill="x", pady=(4, 2))
        # bottom-up so the list can take the remaining space
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        self.install_btn = app._button(btns, "선택한 스킬 설치", self.do_install, primary=True)
        self.install_btn.pack(side="right")
        self.install_btn.config(state="disabled")
        app._button(btns, "닫기", self.close).pack(side="right", padx=8)
        app._label(p, "⚠ 스킬은 에이전트가 그대로 따르는 지시문이며 스크립트를 포함할 수 있습니다. "
                      "신뢰할 수 있는 출처만 설치하세요.", 8, fg=c["warn_fg"], bg=c["bg"], anchor="w", justify="left",
                   wraplength=app.px(self.W - 40)).pack(side="bottom", fill="x", pady=(6, 0))
        self.replace = tk.BooleanVar(value=False)
        app._check(p, "같은 이름이 있으면 교체 (기존 것은 휴지통 폴더에 백업)", self.replace).pack(side="bottom", fill="x")
        self._agent_checks()
        self.results = Scroll(p, c)
        self.results.wrap.pack(fill="both", expand=True, pady=(4, 0))

    def load_link(self) -> None:
        text = self.link.get()
        self.say("불러오는 중… (처음 받는 저장소는 몇 초 걸립니다)")
        self.install_btn.config(state="disabled")

        def work():
            _, root = mgr.fetch(text)
            return mgr.discover(root)
        self.app.bg(work, self._loaded)

    def _loaded(self, res) -> None:
        if not self.win.winfo_exists():
            return
        if isinstance(res, Exception):
            self.say(f"불러오지 못했습니다: {res}", warn=True)
            return
        self._show_found(res)

    def _show_found(self, found: list[Path]) -> None:
        app, c = self.app, self.app.c
        self.results.clear()
        self.picks = {d: tk.BooleanVar(value=len(found) == 1) for d in found}
        if not found:
            self.say("SKILL.md가 들어 있는 폴더를 찾지 못했습니다", warn=True)
            return
        self.say(f"스킬 {len(found)}개를 찾았습니다. 설치할 것을 고르세요.")
        if len(found) > 1:
            head = tk.Frame(self.results.body, bg=c["surface"])
            head.pack(fill="x", padx=10, pady=(6, 0))
            for text, val in (("전체 선택", True), ("전체 해제", False)):
                lab = app._label(head, text, 8, fg=c["accent"], cursor="hand2", padx=4)
                lab.pack(side="left")
                lab.bind("<Button-1>", lambda e, v=val: [x.set(v) for x in self.picks.values()])
        notes = mgr.load_notes()
        for d, var in self.picks.items():
            info = mgr.parse(d / "SKILL.md")
            row = tk.Frame(self.results.body, bg=c["surface"])
            row.pack(fill="x", padx=10, pady=(6, 0))
            app._check(row, d.name, var, bg=c["surface"]).pack(anchor="w")
            app._label(row, brief(d.name, info["desc"], notes), 8, fg=c["muted"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 90)).pack(fill="x", padx=(24, 0))
        self.install_btn.config(state="normal")

    def do_install(self) -> None:
        picks = [d for d, v in self.picks.items() if v.get()]
        agents = [a for a, v in self.agents.items() if v.get()]
        if not picks or not agents:
            self.say("설치할 스킬과 에이전트를 하나 이상 선택하세요", warn=True)
            return
        replace = self.replace.get()
        self.say("설치 중…")
        self.install_btn.config(state="disabled")
        self.app.bg(lambda: mgr.install(picks, agents, replace), self._installed)

    def _installed(self, res) -> None:
        if not self.win.winfo_exists():
            return
        self.install_btn.config(state="normal")
        if isinstance(res, Exception):
            self.say(f"설치 실패: {res}", warn=True)
            return
        n = Counter(r.status for r in res)
        text = f"설치 {n['installed']}건 · 교체 {n['replaced']}건 · 이미 설치됨 {n['exists']}건"
        if n["conflict"]:
            text += f" · 같은 이름이 있어 건너뜀 {n['conflict']}건 (교체를 체크하고 다시 설치하세요)"
        self.say(text, warn=bool(n["conflict"]))
        self.app.say(text)
        self.app.load()

    # -- tab 2: create
    def _build_new(self) -> None:
        app, c, p = self.app, self.app.c, self.panel
        self.name, self.desc = tk.StringVar(), tk.StringVar()
        app._label(p, "스킬 이름 (영문·숫자·-_. , 예: my-skill)", 8, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e = app._entry(p, self.name)
        box.pack(fill="x", pady=(2, 10))
        e.focus_set()
        app._label(p, "한 줄 설명 (에이전트가 언제 쓸지 판단하는 문장)", 8, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e2 = app._entry(p, self.desc)
        box.pack(fill="x", pady=(2, 10))
        e2.bind("<Return>", lambda ev: self.do_create())
        self.status = app._label(p, "", 8, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 40))
        self.status.pack(fill="x")
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        app._button(btns, "만들고 편집기 열기", self.do_create, primary=True).pack(side="right")
        app._button(btns, "닫기", self.close).pack(side="right", padx=8)
        self._agent_checks()

    def do_create(self) -> None:
        agents = [a for a, v in self.agents.items() if v.get()]
        if not agents:
            self.say("에이전트를 하나 이상 선택하세요", warn=True)
            return
        try:
            d, _ = mgr.create(self.name.get().strip(), self.desc.get(), agents)
        except mgr.ManagerError as e:
            self.say(str(e), warn=True)
            return
        self.say(f"만들었습니다: {d}\n편집기에서 SKILL.md 본문을 채워 주세요.")
        self.app.load()
        mgr.open_in_editor(d / "SKILL.md")


_mutex = None  # keeps the named mutex handle alive for the life of the process


def already_running() -> bool:
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "AgentSkillCatalogPanel")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    elif "--make-icon" in sys.argv:
        make_ico(Path(__file__).with_name("skill_viewer.ico"))
    elif not already_running():
        App().run(hidden="--hidden" in sys.argv)
