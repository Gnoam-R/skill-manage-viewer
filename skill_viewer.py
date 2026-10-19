"""SkillDesk: lists the skills each agent (Claude Code / Codex / Antigravity) can use, grouped by
category (category rail on the left, skills on the right), and installs / updates / edits / deletes them.
Lives in a window centered on the screen that is opened and closed from a tray icon.

  pythonw skill_viewer.py            start (window visible; minimize or close hides it to the tray)
  pythonw skill_viewer.py --hidden   start in the tray only
  python  skill_viewer.py --check    print counts per agent/category, no GUI
  python  skill_viewer.py --make-icon  rewrite skill_viewer.ico

Needs: pip install pystray pillow     (the file operations themselves live in skill_manager.py)
Font: Pretendard is loaded from ./fonts/*.otf when present (private to this process); otherwise Malgun Gothic.
"""

import contextlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog

import platform_support as plat
import skill_manager as mgr

HERE = Path(__file__).parent
HOME = Path.home()
NOTES = json.loads((HERE / "skill_notes.json").read_text(encoding="utf-8"))

# Categories are mutually exclusive: every known skill is listed under exactly one of them in
# skill_categories.json (checked by tests). The keywords there only place skills that file doesn't list yet,
# trying the categories in file order, the skill name before its description.
CATALOG = json.loads((HERE / "skill_categories.json").read_text(encoding="utf-8"))
CATEGORIES = [(c["name"], c["keywords"]) for c in CATALOG]
CATEGORY_OF = {skill: c["name"] for c in CATALOG for skill in c["skills"]}
OTHER = "기타"
STAR = "★ 자주 쓰는 스킬"  # rail entry listing what the user starred, separately for each agent
ABOUT = {c["name"]: c["about"] for c in CATALOG} | {
    OTHER: "어느 카테고리에도 맞지 않는 스킬",
    STAR: "☆를 눌러 지정한 스킬만 모아 봅니다",
}
AGENTS = list(mgr.AGENT_DIRS)

# Every category gets its own hue: (text on light, tint on light, text on dark, tint on dark).
CAT_COLORS = {
    "기획·설계": ("#945A06", "#FEF3C7", "#F5C451", "#463813"),
    "구현·테스트": ("#0E6E67", "#D5F3EF", "#4FD1C5", "#173E3B"),
    "디버깅·검증": ("#137538", "#DCF5E3", "#5FD38A", "#1B3D2A"),
    "리뷰·품질": ("#C0261D", "#FDE7E4", "#FF8A80", "#4A2220"),
    "Git·협업": ("#B23B0B", "#FFEBDD", "#FFA566", "#4A2D1A"),
    "UI·디자인": ("#1D4ED8", "#E0E9FF", "#7FA8FF", "#1F3158"),
    "문서·자료": ("#4338CA", "#E5E4FF", "#A5A0FF", "#2B2A5C"),
    "자동화·브라우저": ("#7E22CE", "#F1E3FD", "#C99BFF", "#3B2358"),
    "에이전트·스킬 설정": ("#BE185D", "#FCE4EF", "#FF8FBF", "#4D1F37"),
    OTHER: ("#52525B", "#ECECEF", "#C4C4CC", "#33333A"),
}
# The star entry is not a category, so its colors stay out of CAT_COLORS (a test pairs that with skill_categories.json).
STAR_COLORS = ("#854F0B", "#FAEEDA", "#EF9F27", "#3F2A0A")

LIGHT = dict(bg="#F4F6F9", surface="#FFFFFF", rail="#EBEFF5", line="#CBD3DE", text="#0F172A", muted="#475569",
             accent="#1D5FD0", on_accent="#FFFFFF", soft="#E3ECFC", warn_bg="#FFF1D6", warn_fg="#7A4A00",
             danger="#B42318", danger_bg="#FEECEB")
DARK = dict(bg="#161B22", surface="#1F2630", rail="#1A2029", line="#3A4452", text="#F3F6FA", muted="#C2CBD6",
            accent="#7DB0FF", on_accent="#0B1220", soft="#2A3B55", warn_bg="#4A3A14", warn_fg="#FFD98A",
            danger="#FF8A80", danger_bg="#4A2220")
FONT, FALLBACK_FONT = "Pretendard", plat.FALLBACK_FONT
TRAY_COMMANDS = {"toggle", "add_dialog", "check_updates", "load", "quit"}  # what the tray may ask the app to do
TRAY_NAME = "메뉴 막대" if plat.MAC else "트레이"
DEFAULT_HINT = f"클릭: 이름 복사 · ☆: 자주 쓰는 스킬 지정 · ⋯ 또는 우클릭: 수정·삭제 · {TRAY_NAME} 아이콘으로 열기/닫기"


# ---- data -------------------------------------------------------------------------------------
def tilde(p: Path) -> str:
    """A path under the home folder as ~/...: shorter, and screenshots don't show the account name."""
    text, home = str(p), str(HOME)
    return "~" + text[len(home):] if text.lower().startswith(home.lower()) else text


def categorize(name: str, desc: str) -> str:
    if cat := CATEGORY_OF.get(name) or CATEGORY_OF.get(name.split(":")[-1]):
        return cat
    for text in (name.lower(), desc.lower()):
        for cat, rx in CATEGORIES:
            if re.search(rx, text):
                return cat
    return OTHER


def brief(name: str, desc: str, user_notes: dict[str, str] | None = None) -> str:
    """Korean one-liner of what a skill is for: the user's own note, then the bundled one, then the
    first sentence of its description (the only source for skills that came from somewhere else)."""
    for src in (user_notes or {}, NOTES):
        if n := src.get(name) or src.get(name.split(":")[-1]):
            return n
    first = re.split(r"(?<=[.!?。])\s", desc, maxsplit=1)[0]
    return first if len(first) <= 80 else first[:77] + "…"


def scan(root: Path, prefix: str = "") -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    seen: set[str] = set()
    # follow links: on macOS installed skills are symlinks, which os.walk would otherwise list but never enter
    for dirpath, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:  # a link pointing back up the tree would make the walk endless
            dirs.clear()
            continue
        seen.add(real)
        if "SKILL.md" in files:
            try:
                s = mgr.parse(Path(dirpath) / "SKILL.md")
            except OSError:  # unreadable file: skip that one skill rather than fail the whole list
                dirs.clear()
                continue
            s["name"] = prefix + s["name"]
            s["path"] = Path(dirpath)
            out.append(s)
            dirs.clear()  # don't descend into a skill's own folders
    return out


def claude_plugin_roots() -> list[tuple[Path, str]]:
    """Skill folders of the enabled Claude Code plugins, as (folder, 'plugin:' prefix). Empty if there are none."""
    try:
        installed = json.loads((mgr.CLAUDE_HOME / "plugins/installed_plugins.json").read_text(encoding="utf-8"))["plugins"]
        enabled = json.loads((mgr.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, KeyError, ValueError, TypeError):
        return []
    roots = []
    for key, entries in installed.items():
        with contextlib.suppress(KeyError, IndexError, TypeError):
            if enabled.get(key):
                roots.append((Path(entries[0]["installPath"]) / "skills", key.split("@")[0] + ":"))
    return roots


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


def load_pins() -> dict[str, set[str]]:
    """Starred skill names per agent, from settings.json. Anything malformed counts as 'nothing starred'."""
    raw = mgr.load_settings().get("pins")
    raw = raw if isinstance(raw, dict) else {}
    return {a: {n for n in raw[a] if isinstance(n, str)} if isinstance(raw.get(a), list) else set() for a in AGENTS}


def check() -> None:
    for a in AGENTS:
        skills = collect(a)
        assert all(s["name"] and s["cat"] and s["note"] and s["origin"] for s in skills)
        print(f"{a}: {len(skills)}", dict(Counter(s["cat"] for s in skills)), "| folder:", mgr.AGENT_DIRS[a])
        print("   origin:", dict(Counter(s["origin"].split(" ·")[0] for s in skills)))
        print("   기타:", [s["name"] for s in skills if s["cat"] == OTHER])


# ---- windows helpers ---------------------------------------------------------------------------
def load_bundled_fonts() -> int:
    """Make ./fonts/*.otf|ttf usable by this process only (nothing is installed system-wide)."""
    return sum(plat.register_font(p) for p in sorted((HERE / "fonts").glob("*.[ot]tf")))


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


def tray_menu(put):
    import pystray
    return pystray.Menu(
        pystray.MenuItem("열기 / 닫기", lambda i, m: put("toggle"), default=True),
        pystray.MenuItem("스킬 추가…", lambda i, m: put("add_dialog")),
        pystray.MenuItem("업데이트 확인", lambda i, m: put("check_updates")),
        pystray.MenuItem("새로고침", lambda i, m: put("load")),
        pystray.MenuItem("종료", lambda i, m: put("quit")),
    )


def tray_child() -> None:
    """macOS only: show the menu-bar icon and print the command of each click; exit when the app closes our stdin."""
    import pystray
    icon = pystray.Icon("skill_viewer", icon_image(64), "SkillDesk", tray_menu(lambda cmd: print(cmd, flush=True)))
    threading.Thread(target=lambda: (sys.stdin.read(), icon.stop()), daemon=True).start()
    icon.run()


def make_icns(path: Path) -> None:
    icon_image(1024).save(path, format="ICNS")


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


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
    W, H, RAIL = 900, 700, 230

    def __init__(self, with_tray: bool = True):
        plat.dpi_aware()
        load_bundled_fonts()  # before Tk starts listing fonts
        self.root = root = tk.Tk()
        root.title("SkillDesk")
        from PIL import ImageTk
        self._icon = ImageTk.PhotoImage(icon_image(64))  # keep a reference or Tk drops it
        root.iconphoto(True, self._icon)  # pyright: ignore[reportArgumentType]
        root.resizable(False, True)  # no maximize; minimize and close stay
        self.family = FONT if FONT in tkfont.families(root) else FALLBACK_FONT
        self.scale = plat.pixel_scale(root)
        self.name_font = tkfont.Font(family=self.family, size=round(11 * plat.FONT_SCALE), weight="bold", slant="roman")
        self.tag_font = tkfont.Font(family=self.family, size=round(9 * plat.FONT_SCALE), slant="roman")
        self.theme = mgr.load_settings().get("theme", "light")  # light unless the user switched
        self.c = DARK if self.theme == "dark" else LIGHT
        root.configure(bg=self.c["bg"])
        self.visible = True
        self.cat: str | None = None  # category picked in the rail; None = all
        self.shown: list[dict] = []
        self.sections: dict[str, tk.Frame] = {}  # built category groups of the list; see render()
        self.pins = load_pins()  # starred skill names per agent
        self.icons: dict[str, list[tk.Label]] = {}  # the ☆/★ labels on screen for each skill name (it can be in two groups)
        self.updatable: set[Path] = set()
        self.data: dict[str, list[dict]] = {}
        self.agent = tk.StringVar(value=AGENTS[0])
        self.query = tk.StringVar()
        self.events: queue.Queue = queue.Queue()
        self.tray = None
        self.tray_proc: subprocess.Popen | None = None
        self.dialog: AddDialog | None = None
        self.detail_win: DetailDialog | None = None
        self.popup: Popup | None = None
        self._last_toggle = 0.0
        self._say_job = None
        self._build()
        self.query.trace_add("write", lambda *_: self.render(top=True))
        root.protocol("WM_DELETE_WINDOW", self.hide if with_tray else self.quit)
        root.bind("<Unmap>", self._on_unmap)
        root.bind_all("<MouseWheel>", self._wheel)
        self.load()
        self.place()
        self._dark_titlebar()
        if with_tray:
            self.start_tray()
        root.after(100, self._pump)
        root.after(60_000, self._tick)

    # ---- widgets ----------------------------------------------------------------------------
    def px(self, n: float) -> int:
        return int(n * self.scale)

    def f(self, size: int = 11, bold: bool = False) -> tuple:
        """Font spec. Always upright ('roman'); nothing in this app is ever italic."""
        return (self.family, round(size * plat.FONT_SCALE), "bold" if bold else "normal", "roman")

    def cat_colors(self, cat: str | None) -> tuple[str, str]:
        """(text color, tint) for a category; None (= all) uses the app accent."""
        if cat is None:
            return self.c["accent"], self.c["soft"]
        fg_l, soft_l, fg_d, soft_d = STAR_COLORS if cat == STAR else CAT_COLORS[cat]
        return (fg_d, soft_d) if self.theme == "dark" else (fg_l, soft_l)

    def _label(self, parent, text="", size=11, bold=False, fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=self.f(size, bold), fg=fg or self.c["text"],
                        bg=bg or self.c["surface"], **kw)

    def _icon_button(self, parent, text, command, hint=""):
        b = self._label(parent, text, 11, fg=self.c["muted"], cursor="hand2", padx=9, pady=3)
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: (b.config(bg=self.c["soft"], fg=self.c["accent"]), self.footer.config(text=hint)))
        b.bind("<Leave>", lambda e: (b.config(bg=self.c["surface"], fg=self.c["muted"]),
                                     self.footer.config(text=DEFAULT_HINT, fg=self.c["muted"])))
        return b

    def _button(self, parent, text, command, primary=False, danger=False):
        """A Label dressed as a button: Tk on macOS draws native buttons and ignores their colors."""
        c = self.c
        base = c["accent"] if primary else c["surface"]
        fg = c["on_accent"] if primary else c["danger"] if danger else c["text"]
        b = tk.Label(parent, text=text, font=self.f(11, primary), padx=16, pady=7, cursor="hand2", bg=base, fg=fg,
                     disabledforeground=c["muted"], highlightthickness=0 if primary else 1, highlightbackground=c["line"])

        def on() -> bool:
            return str(b.cget("state")) != "disabled"
        b.bind("<Button-1>", lambda e: command() if on() else None)
        if not primary:
            b.bind("<Enter>", lambda e: b.config(bg=c["danger_bg"] if danger else c["soft"]) if on() else None)
            b.bind("<Leave>", lambda e: b.config(bg=base))
        return b

    def _enable(self, button: tk.Label, on: bool) -> None:
        """A disabled button gets a grey background too, so its grey text never sits on the accent color."""
        c = self.c
        button.config(state="normal" if on else "disabled", bg=c["accent"] if on else c["line"],
                      cursor="hand2" if on else "arrow")

    def _check(self, parent, text, var, bg=None):
        """A checkbox drawn in the app's colors (the system one ignores them). Click or Space toggles `var`."""
        c = self.c
        bg = bg or c["bg"]
        f = tk.Frame(parent, bg=bg, cursor="hand2", takefocus=1, highlightthickness=1, highlightbackground=bg,
                     highlightcolor=c["accent"])
        size = self.px(18)
        box = tk.Canvas(f, width=size, height=size, bg=bg, highlightthickness=0, cursor="hand2")
        box.pack(side="left", padx=(2, self.px(8)), pady=2)
        lab = self._label(f, text, 11, bg=bg, cursor="hand2")
        lab.pack(side="left", padx=(0, 2))
        hover = [False]

        def draw(*_):
            on, s = var.get(), size - 2
            box.delete("all")
            # unchecked border uses `muted`, not `line`: a control's outline needs 3:1 against its background
            rounded_rect(box, 1, 1, s, s, self.px(5), fill=c["accent"] if on else c["surface"],
                         outline=c["accent"] if on or hover[0] else c["muted"], width=max(1, self.px(1.5)))
            if on:
                tick = [round(size * k) for k in (0.27, 0.52, 0.43, 0.68, 0.74, 0.33)]
                box.create_line(*tick, fill=c["on_accent"], width=max(2, self.px(2)), capstyle=tk.ROUND, joinstyle=tk.ROUND)

        def set_hover(v):
            hover[0] = v
            draw()
        for w in (f, box, lab):
            w.bind("<Button-1>", lambda e: (var.set(not var.get()), f.focus_set()))
            w.bind("<Enter>", lambda e: set_hover(True))
            w.bind("<Leave>", lambda e: set_hover(False))
        f.bind("<space>", lambda e: var.set(not var.get()))
        trace = var.trace_add("write", draw)
        # the variable outlives this widget (tabs rebuild it), so stop redrawing a destroyed canvas
        f.bind("<Destroy>", lambda e: var.trace_remove("write", trace) if e.widget is f else None)
        draw()
        f.box = box  # type: ignore[attr-defined]  # for tests
        return f

    def _entry(self, parent, var):
        c = self.c
        box = tk.Frame(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        e = tk.Entry(box, textvariable=var, relief="flat", bg=c["surface"], fg=c["text"],
                     insertbackground=c["text"], font=self.f(11))
        e.pack(fill="x", ipady=7, padx=10)
        return box, e

    def _tab_bar(self, parent, names, on_pick):
        """Underlined tabs. -> (frame, restyle(selected, texts=None))."""
        c = self.c
        bar = tk.Frame(parent, bg=c["surface"])
        cells = {}
        for n in names:
            cell = tk.Frame(bar, bg=c["surface"], cursor="hand2")
            cell.pack(side="left", expand=True, fill="x")
            lab = self._label(cell, n, 11, pady=10)
            lab.pack(fill="x")
            underline = tk.Frame(cell, height=3, bg=c["surface"])
            underline.pack(fill="x")
            for w in (cell, lab):
                w.bind("<Button-1>", lambda e, n=n: on_pick(n))
            cells[n] = (lab, underline)

        def restyle(selected, texts=None):
            for n, (lab, underline) in cells.items():
                on = n == selected
                lab.config(text=(texts or {}).get(n, n), fg=c["accent"] if on else c["muted"], font=self.f(11, on))
                underline.config(bg=c["accent"] if on else c["surface"])
        return bar, restyle

    def _build(self) -> None:
        c = self.c
        header = tk.Frame(self.root, bg=c["surface"])
        header.pack(fill="x")
        titles = tk.Frame(header, bg=c["surface"])
        titles.pack(side="left", padx=(18, 0), pady=(14, 12))
        self._label(titles, "SkillDesk", 16, bold=True).pack(anchor="w")
        self.subtitle = self._label(titles, "", 10, fg=c["muted"])
        self.subtitle.pack(anchor="w")
        actions = tk.Frame(header, bg=c["surface"])
        actions.pack(side="right", padx=(0, 8))
        # created before self.footer exists; the hint handlers only touch it when the pointer is over a button
        for text, cmd, hint in (("＋ 추가", self.add_dialog, "스킬 추가 (링크로 설치 · 새로 만들기)"),
                                ("⇧ 업데이트", lambda: self.check_updates(), "git으로 받은 스킬의 업데이트 확인"),
                                ("⟳", self.load, "목록 새로고침"),
                                ("라이트" if self.theme == "dark" else "다크", self.toggle_theme, "밝은/어두운 화면 전환"),
                                ("–", self.hide, "트레이로 숨기기")):
            self._icon_button(actions, text, cmd, hint).pack(side="left")

        self.tabs, self._restyle_tabs = self._tab_bar(self.root, AGENTS, self.set_agent)
        self.tabs.pack(fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(fill="x")

        self.footer = self._label(self.root, DEFAULT_HINT, 10, fg=c["muted"], bg=c["bg"], anchor="w", padx=16, pady=7)
        self.footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        body = tk.Frame(self.root, bg=c["bg"])
        body.pack(fill="both", expand=True)
        self.rail = tk.Frame(body, bg=c["rail"], width=self.px(self.RAIL))
        self.rail.pack(side="left", fill="y")
        self.rail.pack_propagate(False)
        tk.Frame(body, width=1, bg=c["line"]).pack(side="left", fill="y")
        main = tk.Frame(body, bg=c["bg"])
        main.pack(side="left", fill="both", expand=True)

        search = tk.Frame(main, bg=c["bg"])
        search.pack(fill="x", padx=14, pady=(12, 4))
        box = tk.Frame(search, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"],
                       highlightcolor=c["accent"])
        box.pack(fill="x")
        self._label(box, "검색", 10, fg=c["muted"], padx=10).pack(side="left")
        self.entry = tk.Entry(box, textvariable=self.query, relief="flat", bg=c["surface"], fg=c["text"],
                              insertbackground=c["text"], font=self.f(11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.count = self._label(main, "", 10, fg=c["muted"], bg=c["bg"], anchor="w")
        self.count.pack(fill="x", padx=16, pady=(2, 4))

        self.list = Scroll(main, c)
        self.list.wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
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
        plat.dark_titlebar(win or self.root, self.theme == "dark")

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self.c = DARK if self.theme == "dark" else LIGHT
        mgr.save_setting("theme", self.theme)
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.close()
        for w in self.root.winfo_children():
            w.destroy()
        self.root.configure(bg=self.c["bg"])
        self._build()
        self._dark_titlebar()
        self.render()

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
        data = {a: collect(a) for a in AGENTS}
        if data != self.data:  # rescanning is cheap; redrawing is not, and would also reset what the user is reading
            self.data = data
            self.render()

    def set_agent(self, name: str) -> None:
        self.agent.set(name)
        self.cat = None
        self.render(top=True)

    def pick(self, cat: str | None) -> None:
        self.cat = cat
        self.render(top=True, rebuild=False)

    def render(self, top: bool = False, rebuild: bool = True) -> None:
        """rebuild=False (a category click) keeps the groups already built and only shows the picked ones."""
        c, agent = self.c, self.agent.get()
        q = self.query.get().strip().lower()
        everything = self.data.get(agent, [])
        found = [s for s in everything if q in " ".join((s["name"], s["note"], s["desc"], s["origin"])).lower()]
        counts = Counter(s["cat"] for s in found)
        starred = [s for s in found if s["name"] in self.pins[agent]]
        if self.cat not in counts and self.cat != STAR:  # the picked category vanished (search or refresh)
            self.cat = None
        self.shown = starred if self.cat == STAR else [s for s in found if self.cat in (None, s["cat"])]
        self._restyle_tabs(agent, {a: f"{a}  {len(self.data.get(a, []))}" for a in AGENTS})
        self.subtitle.config(text=f"{agent}에서 사용할 수 있는 스킬")
        self._draw_rail(counts, len(found), len(starred))
        self.count.config(text=f"{len(self.shown)}개 스킬" + (f" · '{q}' 검색 결과" if q else ""))
        if rebuild or not self.shown:  # data, agent, search or theme changed (or only the empty note is up): groups are stale
            self.list.clear()
            self.sections, self.icons = {}, {}
        groups = [(STAR, self.shown)] if self.cat == STAR else [
            (cat, [s for s in self.shown if s["cat"] == cat]) for cat in [n for n, _ in CATEGORIES] + [OTHER]
        ]
        wanted = [self._group(cat, items) for cat, items in groups if items]
        for w in self.body.pack_slaves():
            if w not in wanted:
                w.pack_forget()
        packed = [g for g in wanted if g.winfo_manager()]  # groups already on screen stay put: laying the list out again is the slow part
        prev = None
        for g in wanted:
            if not g.winfo_manager():
                if prev:
                    g.pack(fill="x", after=prev)
                elif packed:
                    g.pack(fill="x", before=packed[0])
                else:
                    g.pack(fill="x")
            prev = g
        if not self.shown:
            msg = ("일치하는 스킬이 없습니다" if everything else
                   f"{agent}에 설치된 스킬이 없습니다.\n\n{mgr.AGENT_DIRS[agent]}\n\n위의 ＋ 추가에서 설치할 수 있습니다.")
            if self.cat == STAR and everything and not q:
                msg = "아직 지정한 스킬이 없습니다\n\n스킬 이름 앞의 ☆를 눌러\n자주 쓰는 스킬로 지정하세요."
            self._label(self.body, msg, 11, fg=c["muted"], pady=40, padx=20, justify="center").pack(fill="x")
        if top:
            self.canvas.yview_moveto(0)

    def _draw_rail(self, counts: Counter, total: int, starred: int) -> None:
        c = self.c
        for w in self.rail.winfo_children():
            w.destroy()
        self._label(self.rail, "카테고리", 10, fg=c["muted"], bg=c["rail"], anchor="w", padx=16, pady=10).pack(fill="x")
        # the star entry is always there, even at 0, so the feature can be found
        for cat, n in [(STAR, starred), (None, total)] + [(k, counts[k]) for k in [n for n, _ in CATEGORIES] + [OTHER] if counts[k]]:
            fg, soft = self.cat_colors(cat)
            on = cat == self.cat
            row = tk.Frame(self.rail, bg=soft if on else c["rail"], cursor="hand2")
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, width=self.px(6), bg=fg)
            bar.pack(side="left", fill="y")
            name = self._label(row, cat or "전체", 11, bold=on, fg=fg if on else c["text"], bg=row["bg"], pady=9)
            name.pack(side="left", padx=(12, 4))
            chip = self._label(row, str(n), 10, bold=True, fg=fg, bg=c["surface"] if on else soft, padx=8)
            chip.pack(side="right", padx=12)
            about = ABOUT.get(cat, "모든 카테고리의 스킬") if cat else "모든 카테고리의 스킬"
            for w in (row, bar, name, chip):
                w.bind("<Button-1>", lambda e, cat=cat: self.pick(cat))
                w.bind("<Enter>", lambda e, t=about: self.footer.config(text=t, fg=c["text"]))
                w.bind("<Leave>", lambda e: self.footer.config(text=DEFAULT_HINT, fg=c["muted"]))

    def _group(self, cat: str, items: list[dict]) -> tk.Frame:
        """One category's header and rows: built the first time it is shown, afterwards only packed or hidden."""
        if cat not in self.sections:
            g = self.sections[cat] = tk.Frame(self.body, bg=self.c["surface"])
            self._section(g, cat, len(items))
            for s in items:
                self._row(g, s, show_cat=cat == STAR)
        return self.sections[cat]

    def _section(self, parent, cat: str, n: int) -> None:
        fg, soft = self.cat_colors(cat)
        f = tk.Frame(parent, bg=soft)
        f.pack(fill="x")
        tk.Frame(f, width=self.px(6), bg=fg).pack(side="left", fill="y")
        self._label(f, cat, 12, bold=True, fg=fg, bg=soft, pady=8, padx=12).pack(side="left")
        self._label(f, f"{n}개", 10, bold=True, fg=fg, bg=soft, padx=10).pack(side="right")
        self._label(f, ABOUT[cat], 10, fg=self.c["text"], bg=soft).pack(side="left")  # what belongs here

    def _fit(self, text: str, room: int) -> str:
        """Shorten text with an ellipsis so it fits in `room` pixels at the row-name font (full name stays in the copy menu)."""
        while len(text) > 1 and self.name_font.measure(text) > room:
            text = text.rstrip("…")[:-1] + "…"
        return text

    def _row(self, parent, s: dict, show_cat: bool = False) -> None:
        c = self.c
        fg, _ = self.cat_colors(s["cat"])
        wrap = tk.Frame(parent, bg=c["surface"], cursor="hand2")
        wrap.pack(fill="x")
        tk.Frame(wrap, width=self.px(6), bg=fg).pack(side="left", fill="y")
        f = tk.Frame(wrap, bg=c["surface"], cursor="hand2")
        f.pack(side="left", fill="x", expand=True)
        top = tk.Frame(f, bg=c["surface"])
        top.pack(fill="x", padx=(14, 8), pady=(9, 0))
        more = self._label(top, "⋯", 13, fg=c["muted"], padx=8)  # right-hand items are packed first so a long name can't push them out
        more.pack(side="right")
        tag = self._label(top, s["origin"], 9, fg=c["muted"])
        tag.pack(side="right")
        # room left for the name once the tag, the ⋯ button and any badges are placed (badge widths are estimates)
        inner = self.px(self.W - self.RAIL - 28 - 6 - 22)
        room = inner - self.tag_font.measure(s["origin"]) - self.px(40)
        room -= self.px(46) * bool(s["manual"]) + self.px(88) * (s["repo"] in self.updatable)
        star = self._label(top, "", 13, cursor="hand2", padx=2)  # a click on it stars / unstars the skill
        self._paint_star(star, s["name"] in self.pins[s["agent"]])
        star.pack(side="left", padx=(0, 4))
        self.icons.setdefault(s["name"], []).append(star)
        extra = [star]  # labels that tint on hover together with the rest of the row
        room -= self.px(30)
        if show_cat:  # in the star list the colour bar alone doesn't say which category a skill came from
            room -= self.tag_font.measure(s["cat"]) + self.px(24)
        name = self._label(top, self._fit(s["name"], room), 11, bold=True, anchor="w")
        name.pack(side="left")
        if show_cat:
            extra.append(self._label(top, s["cat"], 9, fg=fg))
            extra[-1].pack(side="left", padx=8)
        if s["manual"]:
            self._label(top, "수동", 9, fg=c["warn_fg"], bg=c["warn_bg"], padx=6).pack(side="left", padx=8)
        if s["repo"] in self.updatable:
            self._label(top, "업데이트 있음", 9, bold=True, fg=c["on_accent"], bg=c["accent"], padx=6).pack(side="left", padx=2)
        note = self._label(f, s["note"], 10, fg=c["muted"], anchor="w", justify="left", wraplength=inner - self.px(28))
        note.pack(fill="x", padx=(14, 12), pady=(2, 9))
        tk.Frame(parent, height=1, bg=c["line"]).pack(fill="x")
        parts = (wrap, f, top, name, tag, more, note, *extra)

        def tint(color):
            for w in parts:
                w.config(bg=color)
        for w in (*parts, *top.winfo_children()):
            w.bind("<Enter>", lambda e: tint(c["soft"]))
            w.bind("<Leave>", lambda e: tint(c["surface"]))
            w.bind("<Button-1>", lambda e, n=s["name"]: self.copy(n))
            w.bind("<Double-Button-1>", lambda e, s=s: self.detail(s))
            for seq in plat.RIGHT_CLICK:
                w.bind(seq, lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        more.bind("<Button-1>", lambda e, s=s: self.row_menu(s, e.x_root, e.y_root))
        star.bind("<Button-1>", lambda e, s=s: self.toggle_pin(s))  # after the loop above, which gave every label "copy the name"
        # a quick second click on the star is still a star click, not the row's double-click (which opens the detail page)
        star.bind("<Double-Button-1>", lambda e, s=s: self.toggle_pin(s))

    def copy(self, name: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(name)
        self.say(f"복사됨: {name}", ms=2000)

    def toggle_pin(self, s: dict) -> None:
        pins = self.pins[s["agent"]]
        pins ^= {s["name"]}
        mgr.save_setting("pins", {a: sorted(p) for a, p in self.pins.items()})
        self.say(f"★ 추가됨: {s['name']}" if s["name"] in pins else f"★ 해제됨: {s['name']}")
        live = self.icons[s["name"]] = [w for w in self.icons.get(s["name"], []) if w.winfo_exists()]
        for w in live:  # repaint in place: rebuilding the skill's whole category just to flip one icon is slow
            self._paint_star(w, s["name"] in pins)
        if g := self.sections.pop(STAR, None):  # the star list itself has different rows now
            g.destroy()
        self.render(rebuild=False)

    def _paint_star(self, label: tk.Label, starred: bool) -> None:
        label.config(text="★" if starred else "☆", fg=self.cat_colors(STAR)[0] if starred else self.c["muted"])

    # ---- row actions ------------------------------------------------------------------------
    def menu_items(self, s: dict) -> list[tuple[str, object, str]]:
        """(label, action, kind) for a skill's ⋯ menu. kind: '' | 'danger' | 'note' (no action) | '-' (separator)."""
        starred = s["name"] in self.pins[s["agent"]]
        items: list[tuple[str, object, str]] = [
            ("상세 보기", lambda: self.detail(s), ""),
            ("이름 복사", lambda: self.copy(s["name"]), ""),
            ("★ 자주 쓰는 스킬에서 해제" if starred else "★ 자주 쓰는 스킬에 추가", lambda: self.toggle_pin(s), ""),
            ("", None, "-"),
            ("용도 설명 수정…", lambda: self.edit_note(s), ""),
            ("SKILL.md 열어서 수정" if s["managed"] else "SKILL.md 열기 (보기)", lambda: self.edit_file(s), ""),
            ("", None, "-"),
        ]
        if s["managed"]:
            if s["repo"]:
                items.append(("이 스킬의 저장소 업데이트 확인", lambda: self.check_updates([s["repo"]]), ""))
            items.append(("삭제…", lambda: self.delete(s), "danger"))
        else:
            items.append((f"{s['origin']} 제공 스킬이라 여기서 삭제할 수 없습니다", None, "note"))
        return items

    def row_menu(self, s: dict, x: int, y: int) -> None:
        self.close_popup()
        self.popup = Popup(self, self.menu_items(s), x, y)

    def close_popup(self) -> None:
        if self.popup:
            self.popup.close()
            self.popup = None

    def detail(self, s: dict) -> None:
        self.close_popup()
        if self.detail_win and self.detail_win.win.winfo_exists():
            self.detail_win.close()
        self.detail_win = DetailDialog(self, s)

    def _side_window(self, title: str, width: int, height: int) -> tk.Toplevel:
        """A themed window centered over the panel (kept on screen), for dialogs."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=self.c["bg"])
        win.transient(self.root)  # stays above the panel without being topmost
        w, h = self.px(width), min(self.px(height), win.winfo_screenheight() - 80)
        left, top, right, bottom = plat.work_area(self.root)
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        win.geometry(f"{w}x{h}+{max(left, min(x, right - w))}+{max(top, min(y, bottom - h))}")
        self._dark_titlebar(win)
        return win

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
        """Size the window and put it in the middle of the usable screen (what a normal app does the first time)."""
        left, top, right, bottom = plat.work_area(self.root)
        w, h = min(self.px(self.W), right - left - 24), min(self.px(self.H), bottom - top - 24)
        self.root.geometry(f"{w}x{h}+{left + (right - left - w) // 2}+{top + (bottom - top - h) // 2}")

    def show(self) -> None:
        self.root.deiconify()  # it comes back where the user left it; only the first start centers it
        self.root.lift()
        self.root.attributes("-topmost", True)  # Windows often refuses a plain lift() from the tray; pop to the front once...
        self.root.attributes("-topmost", False)  # ...then behave like a normal window (clicking another app puts it behind)
        self.root.focus_force()
        self.visible = True
        self.load()

    def hide(self) -> None:
        self.root.withdraw()
        self.visible = False

    def _on_unmap(self, e) -> None:
        # the minimize button: go to the tray, not the taskbar / Dock. macOS reports 'iconic' only after the
        # unmap event, so look a moment later.
        if e.widget is self.root:
            self.root.after(150, lambda: self.hide() if self.root.state() == "iconic" else None)

    def toggle(self) -> None:
        # a double-click on the tray icon arrives as two clicks: acting on both showed the window and hid it again
        # The clock starts when the first click has been handled: showing reloads the list, which can take longer than
        # the gap between the two clicks.
        if time.monotonic() - self._last_toggle < 0.6:
            return
        self.hide() if self.visible else self.show()
        self._last_toggle = time.monotonic()

    def quit(self) -> None:
        if self.tray:
            self.tray.stop()
        if self.tray_proc:
            self.tray_proc.terminate()
        self.root.destroy()

    def start_tray(self) -> None:
        if plat.MAC:
            # pystray must own the main thread on macOS and Tk needs it too, so the menu-bar icon lives in its own
            # process and reports clicks as one command per line.
            self.tray_proc = subprocess.Popen([sys.executable, str(HERE / "skill_viewer.py"), "--tray-child"],
                                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            threading.Thread(target=self._read_tray, daemon=True).start()
            return
        import pystray
        # tray callbacks run on pystray's thread; the Tk thread drains the queue
        self.tray = pystray.Icon("skill_viewer", icon_image(64), "SkillDesk", tray_menu(self.events.put))
        self.tray.run_detached()

    def _read_tray(self) -> None:
        if self.tray_proc and self.tray_proc.stdout:
            for line in self.tray_proc.stdout:
                if line.strip() in TRAY_COMMANDS:  # never act on anything else a child process prints
                    self.events.put(line.strip())

    def _pump(self) -> None:
        while not self.events.empty():
            item = self.events.get_nowait()
            if callable(item):
                item()
            elif item in TRAY_COMMANDS:
                getattr(self, item)()
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
    W, H = 540, 720

    def __init__(self, app: App):
        self.app, c = app, app.c
        self.win = win = app._side_window("스킬 추가", self.W, self.H)
        self.mode = "link"
        self.picks: dict[Path, tk.BooleanVar] = {}
        self.agents = {a: tk.BooleanVar(value=True) for a in AGENTS}
        bar, self._restyle = app._tab_bar(win, ["링크로 설치", "새로 만들기"], self.show)
        bar.pack(fill="x")
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")
        self.panel = tk.Frame(win, bg=c["bg"])
        self.panel.pack(fill="both", expand=True, padx=14, pady=12)
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
        row.pack(side="bottom", fill="x", pady=(8, 0))
        app._label(row, "설치할 에이전트", 10, fg=c["muted"], bg=c["bg"]).pack(anchor="w")
        for a, var in self.agents.items():
            app._check(row, a, var).pack(side="left", padx=(0, 12))

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
        app._label(p, "GitHub 저장소·폴더·SKILL.md 링크 또는 owner/repo[/경로]", 10, fg=c["muted"], bg=c["bg"],
                   anchor="w").pack(fill="x", pady=(5, 0))
        self.status = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 50))
        self.status.pack(fill="x", pady=(5, 3))
        # bottom-up so the list can take the remaining space
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        self.install_btn = app._button(btns, "선택한 스킬 설치", self.do_install, primary=True)
        self.install_btn.pack(side="right")
        self.app._enable(self.install_btn, False)
        app._button(btns, "닫기", self.close).pack(side="right", padx=8)
        app._label(p, "⚠ 스킬은 에이전트가 그대로 따르는 지시문이며 스크립트를 포함할 수 있습니다. "
                      "신뢰할 수 있는 출처만 설치하세요.", 10, fg=c["warn_fg"], bg=c["bg"], anchor="w", justify="left",
                   wraplength=app.px(self.W - 50)).pack(side="bottom", fill="x", pady=(8, 0))
        self.replace = tk.BooleanVar(value=False)
        app._check(p, "같은 이름이 있으면 교체 (기존 것은 휴지통 폴더에 백업)", self.replace).pack(side="bottom", fill="x")
        self._agent_checks()
        self.results = Scroll(p, c)
        self.results.wrap.pack(fill="both", expand=True, pady=(4, 0))

    def load_link(self) -> None:
        text = self.link.get()
        self.say("불러오는 중… (처음 받는 저장소는 몇 초 걸립니다)")
        self.app._enable(self.install_btn, False)

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
            head.pack(fill="x", padx=10, pady=(8, 0))
            for text, val in (("전체 선택", True), ("전체 해제", False)):
                lab = app._label(head, text, 10, bold=True, fg=c["accent"], cursor="hand2", padx=4)
                lab.pack(side="left")
                lab.bind("<Button-1>", lambda e, v=val: [x.set(v) for x in self.picks.values()])
        notes = mgr.load_notes()
        for d, var in self.picks.items():
            info = mgr.parse(d / "SKILL.md")
            row = tk.Frame(self.results.body, bg=c["surface"])
            row.pack(fill="x", padx=10, pady=(8, 0))
            app._check(row, d.name, var, bg=c["surface"]).pack(anchor="w")
            app._label(row, brief(d.name, info["desc"], notes), 10, fg=c["muted"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 100)).pack(fill="x", padx=(26, 0))
        self.app._enable(self.install_btn, True)

    def do_install(self) -> None:
        picks = [d for d, v in self.picks.items() if v.get()]
        agents = [a for a, v in self.agents.items() if v.get()]
        if not picks or not agents:
            self.say("설치할 스킬과 에이전트를 하나 이상 선택하세요", warn=True)
            return
        replace = self.replace.get()
        self.say("설치 중…")
        self.app._enable(self.install_btn, False)
        self.app.bg(lambda: mgr.install(picks, agents, replace), self._installed)

    def _installed(self, res) -> None:
        if not self.win.winfo_exists():
            return
        self.app._enable(self.install_btn, True)
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
        app._label(p, "스킬 이름 (영문·숫자·-_. , 예: my-skill)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e = app._entry(p, self.name)
        box.pack(fill="x", pady=(3, 12))
        e.focus_set()
        app._label(p, "한 줄 설명 (에이전트가 언제 쓸지 판단하는 문장)", 10, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        box, e2 = app._entry(p, self.desc)
        box.pack(fill="x", pady=(3, 12))
        e2.bind("<Return>", lambda ev: self.do_create())
        self.status = app._label(p, "", 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                                 wraplength=app.px(self.W - 50))
        self.status.pack(fill="x")
        btns = tk.Frame(p, bg=c["bg"])
        btns.pack(side="bottom", fill="x", pady=(10, 0))
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


class Popup:
    """The ⋯ menu, drawn in the app's colors (the system menu can't be themed). Closes on pick, Esc or click-away."""

    def __init__(self, app: App, items: list[tuple[str, object, str]], x: int, y: int):
        c = app.c
        self.app = app
        self.win = win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=c["line"])
        box = tk.Frame(win, bg=c["surface"])
        box.pack(padx=1, pady=1)  # the 1px gap shows the line color as a border
        tk.Frame(box, height=app.px(4), bg=c["surface"]).pack(fill="x")
        self.rows: list[tk.Label] = []
        for label, action, kind in items:
            if kind == "-":
                tk.Frame(box, height=1, bg=c["line"]).pack(fill="x", padx=10, pady=4)
                continue
            fg = c["danger"] if kind == "danger" else c["muted"] if kind == "note" else c["text"]
            row = app._label(box, label, 11, fg=fg, anchor="w", padx=16, pady=7, cursor="hand2" if action else "arrow")
            row.pack(fill="x")
            self.rows.append(row)
            if callable(action):
                hover_bg, hover_fg = (c["danger_bg"], fg) if kind == "danger" else (c["soft"], c["accent"])
                row.bind("<Enter>", lambda e, r=row, b=hover_bg, h=hover_fg: r.config(bg=b, fg=h))
                row.bind("<Leave>", lambda e, r=row, f=fg: r.config(bg=c["surface"], fg=f))
                row.bind("<Button-1>", lambda e, a=action: self.pick(a))
        tk.Frame(box, height=app.px(4), bg=c["surface"]).pack(fill="x")
        win.update_idletasks()
        left, top, right, bottom = plat.work_area(app.root)
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = max(left, min(x, right - w - 4))
        y = y if y + h < bottom else max(top, y - h)  # open upward near the bottom of the screen
        win.geometry(f"+{x}+{y}")
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", lambda e: self.close())
        win.focus_force()

    def pick(self, action) -> None:
        self.close()
        action()

    def close(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()


class DetailDialog:
    """Everything about one skill: purpose, category, origin and version, where it lives, which agents have it,
    its files and its SKILL.md, with the actions that apply to it."""

    W, H = 600, 760

    def __init__(self, app: App, s: dict):
        self.app, self.s, c = app, s, app.c
        self.win = win = app._side_window(f"{s['name']} 상세", self.W, self.H)
        win.protocol("WM_DELETE_WINDOW", self.close)
        fg, soft = app.cat_colors(s["cat"])

        head = tk.Frame(win, bg=c["surface"])
        head.pack(fill="x")
        tk.Frame(head, height=app.px(4), bg=fg).pack(fill="x")
        app._label(head, s["name"], 16, bold=True, anchor="w", justify="left", wraplength=app.px(self.W - 40),
                   padx=20, pady=(12)).pack(fill="x")
        chips = tk.Frame(head, bg=c["surface"])
        chips.pack(fill="x", padx=20, pady=(0, 12))
        app._label(chips, s["cat"], 10, bold=True, fg=fg, bg=soft, padx=8, pady=2).pack(side="left")
        app._label(chips, s["origin"], 10, fg=c["muted"], bg=c["rail"], padx=8, pady=2).pack(side="left", padx=6)
        if s["manual"]:
            app._label(chips, "수동", 10, fg=c["warn_fg"], bg=c["warn_bg"], padx=8, pady=2).pack(side="left")
        if s["repo"] in app.updatable:
            app._label(chips, "업데이트 있음", 10, bold=True, fg=c["on_accent"], bg=c["accent"], padx=8, pady=2).pack(side="left", padx=6)
        tk.Frame(win, height=1, bg=c["line"]).pack(fill="x")

        # actions at the bottom first, so the SKILL.md view can take whatever height is left. Two rows: six buttons
        # don't fit one line of this window.
        bottom = tk.Frame(win, bg=c["bg"])
        bottom.pack(side="bottom", fill="x", padx=16, pady=(8, 12))
        tools = tk.Frame(bottom, bg=c["bg"])
        tools.pack(fill="x")
        app._button(tools, "SKILL.md 수정" if s["managed"] else "SKILL.md 열기", lambda: app.edit_file(s)).pack(side="left")
        app._button(tools, "폴더 열기", lambda: plat.open_folder(s["path"])).pack(side="left", padx=(8, 0))
        app._button(tools, "이름 복사", lambda: app.copy(s["name"])).pack(side="left", padx=(8, 0))
        if s["repo"]:
            app._button(tools, "업데이트 확인", lambda: app.check_updates([s["repo"]])).pack(side="left", padx=(8, 0))
        ends = tk.Frame(bottom, bg=c["bg"])
        ends.pack(fill="x", pady=(8, 0))
        if s["managed"]:
            app._button(ends, "삭제…", self.delete, danger=True).pack(side="left")
        app._button(ends, "닫기", self.close).pack(side="right")
        tk.Frame(win, height=1, bg=c["line"]).pack(side="bottom", fill="x")

        body = tk.Frame(win, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        app._label(body, s["note"], 12, bold=True, bg=c["bg"], anchor="w", justify="left",
                   wraplength=app.px(self.W - 40)).pack(fill="x")
        if s["desc"] and s["desc"] != s["note"]:
            app._label(body, s["desc"], 10, fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 40)).pack(fill="x", pady=(4, 0))
        grid = tk.Frame(body, bg=c["bg"])
        grid.pack(fill="x", pady=(14, 10))
        for i, (key, value) in enumerate(self.facts()):
            app._label(grid, key, 10, fg=c["muted"], bg=c["bg"], anchor="nw").grid(row=i, column=0, sticky="nw", pady=3)
            app._label(grid, value, 10, bg=c["bg"], anchor="w", justify="left",
                       wraplength=app.px(self.W - 160)).grid(row=i, column=1, sticky="w", padx=(14, 0), pady=3)
        app._label(body, "SKILL.md", 10, bold=True, fg=c["muted"], bg=c["bg"], anchor="w").pack(fill="x")
        frame = tk.Frame(body, bg=c["surface"], highlightthickness=1, highlightbackground=c["line"])
        frame.pack(fill="both", expand=True, pady=(4, 0))
        self.text = tk.Text(frame, wrap="word", relief="flat", bd=0, padx=12, pady=10, font=app.f(10),
                            bg=c["surface"], fg=c["text"], insertbackground=c["text"], height=8)
        self.text.pack(fill="both", expand=True)  # scrolls with the wheel; the system scrollbar wouldn't match the theme
        try:
            content = (s["path"] / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            content = f"읽지 못했습니다: {e}"
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")  # a viewer; editing goes through the editor button

    def facts(self) -> list[tuple[str, str]]:
        app, s = self.app, self.s
        p = s["path"]
        where = tilde(p) + (f"\n→ {tilde(mgr.target_of(p))}" if mgr.is_link(p) else "")
        agents = [a for a in AGENTS if any(x["name"] == s["name"] for x in app.data.get(a, []))]
        files = sorted(str(f.relative_to(p)) for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
        shown = ", ".join(files[:8]) + (f" 외 {len(files) - 8}개" if len(files) > 8 else "")
        facts = [("카테고리", f"{s['cat']} — {ABOUT.get(s['cat'], '')}"), ("출처", s["origin"])]
        if s["repo"]:
            facts += [("저장소", mgr.repo_name(s["repo"])), ("버전", mgr.version(s["repo"]))]
        facts += [
            ("호출", "사용자가 /이름으로 직접 불러야 함 (수동)" if s["manual"] else "에이전트가 상황에 맞으면 스스로 사용"),
            ("설치된 에이전트", ", ".join(agents) or "-"),
            ("위치", where),
            (f"파일 {len(files)}개", shown or "-"),
        ]
        return facts

    def delete(self) -> None:
        before = self.app.data
        self.app.delete(self.s)
        if self.app.data is not before:  # the list was reloaded, so the skill is gone
            self.close()

    def close(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()


if __name__ == "__main__":
    if "--tray-child" in sys.argv:
        tray_child()
    elif "--check" in sys.argv:
        check()
    elif "--make-icon" in sys.argv:
        make_ico(HERE / "skill_viewer.ico")
    elif "--make-icns" in sys.argv:
        make_icns(HERE / "skill_viewer.icns")
    elif not plat.already_running():
        App().run(hidden="--hidden" in sys.argv)
