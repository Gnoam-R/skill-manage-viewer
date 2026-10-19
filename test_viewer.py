"""python -m unittest test_viewer -v

Drives the panel in-process (no synthetic mouse/keyboard input) against temp agent folders, so the real
~/.claude, ~/.codex and ~/.gemini are only read for plugin/built-in skills and never written.
"""

import ctypes
import json
import os
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import font as tkfont
from unittest import mock

from PIL import ImageGrab

import platform_support as plat
import skill_manager as mgr
import skill_viewer as v

SHOTS = Path(tempfile.gettempdir())


def write_skill(d: Path, desc: str = "does a thing") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def texts(w):
    return [x.cget("text") for x in walk(w) if isinstance(x, tk.Label)]


def find(app: v.App, agent: str, name: str) -> dict | None:
    return next((s for s in app.data[agent] if s["name"] == name), None)


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls.tmp = Path(cls._tmp.name).resolve()
        cls._patches = [
            mock.patch.object(mgr, "STORE", t / "store"),
            mock.patch.object(mgr, "AGENT_DIRS", {"Claude Code": t / "claude", "Codex": t / "codex", "Antigravity": t / "agy"}),
            mock.patch.object(mgr, "UPDATER", t / "updater.ps1"),
            mock.patch.object(mgr, "open_in_editor"),
        ]
        for p in cls._patches:
            p.start()
        write_skill(t / "codex/legacy")  # a real folder the user made by hand
        cls.repo = t / "gitrepo"  # a git-backed skill linked into Claude Code
        write_skill(cls.repo / "skills/gitskill")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q", str(cls.repo)], check=True, capture_output=True)
        subprocess.run([*git, "add", "-A"], cwd=cls.repo, check=True, capture_output=True)
        subprocess.run([*git, "commit", "-qm", "init"], cwd=cls.repo, check=True, capture_output=True)
        mgr.install([cls.repo / "skills/gitskill"], ["Claude Code"])
        cls.app = v.App(with_tray=plat.WIN)  # the macOS tray is a child process; CI has no menu bar to show it in
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()
        for d in mgr.AGENT_DIRS.values():  # unlink junctions first so cleanup can't walk into a clone through them
            for p in d.iterdir() if d.is_dir() else []:
                if mgr.is_link(p):
                    plat.unlink_dir(p)
        for p in cls._patches:
            p.stop()
        cls._tmp.cleanup()

    def settle(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            self.app.root.update()
            self.app._pump()
            if cond():
                return
            time.sleep(0.05)
        self.fail("timed out waiting for the UI")

    def shot(self, win, name):
        win.update()
        time.sleep(0.7)  # let Windows finish its window fade-in, or the grab is half transparent
        win.update()
        box = (win.winfo_rootx(), win.winfo_rooty(), win.winfo_rootx() + win.winfo_width(), win.winfo_rooty() + win.winfo_height())
        if plat.WIN:  # macOS asks for screen-recording permission, which a test run can not give
            ImageGrab.grab(bbox=box).save(SHOTS / f"{name}.png")
        return box

    def rows(self):
        return len(self.app.shown)

    # ---- window, tray, list ---------------------------------------------------------------
    def test_01_tray_corner_and_toggle(self):
        app = self.app
        if plat.WIN:
            self.assertTrue(app.tray is not None and app.tray.HAS_MENU)
        left, top, right, bottom = plat.work_area(app.root)
        x, y, x2, y2 = self.shot(app.root, "t1_default")
        self.assertTrue(app.visible and x2 <= right and y >= top)
        app.move()
        box = self.shot(app.root, "t1_bottomleft")
        self.assertTrue(box[0] < 40 and box[3] >= bottom - 40, box)
        app.move()
        app.events.put("toggle")
        app._pump()
        self.assertTrue(not app.visible and app.root.state() == "withdrawn")
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_01b_window_has_minimize_and_close_but_no_toolwindow_style(self):
        app = self.app
        app.root.update()
        if plat.WIN:
            hwnd = ctypes.windll.user32.GetParent(app.root.winfo_id())
            style, ex = ctypes.windll.user32.GetWindowLongW(hwnd, -16), ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            self.assertTrue(style & 0x20000, "WS_MINIMIZEBOX missing")  # the button the user asked for
            self.assertFalse(ex & 0x80, "WS_EX_TOOLWINDOW would hide the minimize button")
        app.root.iconify()  # minimizing sends the window to the tray instead of leaving it on the taskbar
        self.settle(lambda: not app.visible)
        self.assertEqual(app.root.state(), "withdrawn")
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_02_origin_labels(self):
        legacy = find(self.app, "Codex", "legacy")
        self.assertEqual((legacy["origin"], legacy["managed"], legacy["repo"]), ("로컬", True, None))
        g = find(self.app, "Claude Code", "gitskill")
        self.assertTrue(g["origin"].startswith("git · ") and g["repo"] == self.repo.resolve() and g["managed"])

    def test_03_tabs_search_rail(self):
        app = self.app
        app.set_agent("Claude Code")
        app.query.set("gitskill")
        app.root.update()
        self.assertEqual(self.rows(), 1)
        app.query.set("zzzz-no-match")
        app.root.update()
        self.assertEqual(self.rows(), 0)
        app.query.set("git ·")
        app.root.update()  # origin text is searchable too
        self.assertIn("gitskill", [s["name"] for s in app.shown])
        self.assertGreater(self.rows(), 0)
        app.query.set("")
        total = self.rows()
        cats = sorted({s["cat"] for s in app.data["Claude Code"]})
        self.assertGreater(len(cats), 1)
        app.pick(cats[0])  # clicking a category in the rail lists only that category
        self.assertTrue(all(s["cat"] == cats[0] for s in app.shown))
        self.assertTrue(0 < self.rows() < total)
        self.shot(app.root, "t3_category")
        app.pick(None)
        self.assertEqual(self.rows(), total)
        app.pick(cats[0])
        app.query.set("zzzz-no-match")  # the picked category has no match: fall back to all, not an empty rail
        self.assertIsNone(app.cat)
        app.query.set("")
        self.shot(app.root, "t3_list")
        app.copy("grilling")
        self.assertEqual(app.root.clipboard_get(), "grilling")

    def test_03a_every_category_has_its_own_color(self):
        app = self.app
        for theme in ("light", "dark"):
            app.theme = theme
            colors = [app.cat_colors(c) for c in v.CAT_COLORS]
            self.assertEqual(len(set(colors)), len(v.CAT_COLORS), theme)  # no two categories share a color pair
            self.assertEqual(len({fg for fg, _ in colors}), len(v.CAT_COLORS), theme)
        app.theme = "light"

    def test_03b_long_names_are_shortened_not_wrapped(self):
        app = self.app
        self.assertEqual(app._fit("tdd", 200), "tdd")
        long = "superpowers:finishing-a-development-branch"
        out = app._fit(long, 150)
        self.assertTrue(out.endswith("…") and out != long and app.name_font.measure(out) <= 150, out)
        self.assertEqual(app._fit(long, 10_000), long)
        self.assertTrue(app._fit("x" * 500, 1))  # never loops forever or returns nothing

    def test_04_menu_entries(self):
        app = self.app
        menu = app.build_menu(find(app, "Codex", "legacy"))
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
        self.assertIn("삭제…", labels)
        self.assertIn("SKILL.md 열어서 수정", labels)
        gm = app.build_menu(find(app, "Claude Code", "gitskill"))
        self.assertIn(
            "이 스킬의 저장소 업데이트 확인", [gm.entrycget(i, "label") for i in range(gm.index("end") + 1) if gm.type(i) == "command"]
        )
        ro = next((s for s in app.data["Claude Code"] if not s["managed"]), None)
        if ro:  # plugin / app-synced skills can't be deleted from here
            m = app.build_menu(ro)
            self.assertNotIn("삭제…", [m.entrycget(i, "label") for i in range(m.index("end") + 1) if m.type(i) == "command"])

    def test_05_edit_note(self):
        app = self.app
        with mock.patch.object(v.simpledialog, "askstring", return_value="내가 쓴 설명"):
            app.edit_note(find(app, "Codex", "legacy"))
        self.assertEqual(find(app, "Codex", "legacy")["note"], "내가 쓴 설명")
        with mock.patch.object(v.simpledialog, "askstring", return_value=""):
            app.edit_note(find(app, "Codex", "legacy"))
        self.assertNotEqual(find(app, "Codex", "legacy")["note"], "내가 쓴 설명")

    # ---- add: create / link ----------------------------------------------------------------
    def test_06_add_dialog_create(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog
        dlg.show("새로 만들기")
        dlg.name.set("bad name")
        dlg.do_create()
        self.assertIn("영문", dlg.status.cget("text"))
        dlg.name.set("my-skill")
        dlg.desc.set("테스트용 스킬")
        dlg.do_create()
        for d in mgr.AGENT_DIRS.values():
            self.assertTrue((d / "my-skill/SKILL.md").is_file())
        self.assertEqual(find(app, "Codex", "my-skill")["origin"], "내 스킬")
        mgr.open_in_editor.assert_called()
        self.shot(dlg.win, "t6_dialog_new")
        dlg.close()

    def test_07_add_dialog_link(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog
        dlg.show("링크로 설치")
        dlg.link.set("file:///c:/evil")
        dlg.load_link()
        self.settle(lambda: "https" in dlg.status.cget("text"))  # rejected without touching git or network
        pack = self.tmp / "pack"
        for n in ("pa", "pb"):
            write_skill(pack / n, f"{n} skill")
        dlg._show_found(mgr.discover(pack))
        self.shot(dlg.win, "t7_dialog_link")
        dlg.do_install()  # nothing ticked yet
        self.assertIn("선택", dlg.status.cget("text"))
        dlg.picks[pack / "pb"].set(True)
        dlg.agents["Antigravity"].set(False)
        dlg.do_install()
        self.settle(lambda: "설치 2건" in dlg.status.cget("text"))
        self.assertTrue((mgr.AGENT_DIRS["Claude Code"] / "pb/SKILL.md").is_file())
        self.assertFalse((mgr.AGENT_DIRS["Antigravity"] / "pb").exists())
        write_skill(mgr.AGENT_DIRS["Codex"] / "pa")  # a real folder in the way
        dlg.picks[pack / "pa"].set(True)
        dlg.do_install()
        self.settle(lambda: "건너뜀" in dlg.status.cget("text"))
        self.assertEqual(mgr.parse(mgr.AGENT_DIRS["Codex"] / "pa/SKILL.md")["desc"], "does a thing")  # untouched
        dlg.replace.set(True)
        dlg.do_install()
        self.settle(lambda: "교체 1건" in dlg.status.cget("text"))
        self.assertEqual(mgr.parse(mgr.AGENT_DIRS["Codex"] / "pa/SKILL.md")["desc"], "pa skill")
        dlg.close()

    # ---- update / delete --------------------------------------------------------------------
    def test_08_check_and_apply_updates(self):
        app = self.app
        calls = []
        with (
            mock.patch.object(mgr, "has_update", return_value=True),
            mock.patch.object(mgr, "update", side_effect=lambda r: calls.append(r) or {"old": "aaa", "new": "bbb", "gone": []}),
            mock.patch.object(v.messagebox, "askyesno", return_value=True),
            mock.patch.object(v.messagebox, "showinfo") as info,
        ):
            app.check_updates()
            self.settle(lambda: info.called)
        self.assertEqual(calls, [self.repo.resolve()])
        self.assertIn("aaa → bbb", info.call_args[0][1])
        self.assertEqual(app.updatable, set())

    def test_09_update_check_marks_rows_and_survives_decline(self):
        app = self.app
        with (
            mock.patch.object(mgr, "has_update", return_value=True),
            mock.patch.object(mgr, "update") as upd,
            mock.patch.object(v.messagebox, "askyesno", return_value=False),
        ):
            app.check_updates([self.repo.resolve()])
            self.settle(lambda: app.updatable)
        upd.assert_not_called()
        app.set_agent("Claude Code")
        app.render()
        self.assertIn("업데이트 있음", texts(app.body))
        self.shot(app.root, "t9_update_badge")
        app.updatable.clear()

    def test_10_delete(self):
        app = self.app
        with mock.patch.object(v.messagebox, "askyesno", return_value=False):
            app.delete(find(app, "Codex", "legacy"))
        self.assertIsNotNone(find(app, "Codex", "legacy"))  # declined: nothing happens
        with mock.patch.object(v.messagebox, "askyesno", return_value=True):
            app.delete(find(app, "Codex", "legacy"))
            app.delete(find(app, "Claude Code", "gitskill"))
        self.assertIsNone(find(app, "Codex", "legacy"))
        self.assertIsNone(find(app, "Claude Code", "gitskill"))
        self.assertTrue((self.repo / "skills/gitskill/SKILL.md").is_file())  # a link was removed, not the source
        trashed = [p.name.split("_", 2)[2] for p in (mgr.STORE / "trash").iterdir()]
        self.assertIn("legacy", trashed)  # the real folder was moved, not destroyed
        self.assertNotIn("gitskill", trashed)  # a link is only unlinked

    # ---- theme, fonts, discovery ------------------------------------------------------------
    def test_11_theme_toggle_is_saved_and_rebuilds(self):
        app = self.app
        self.assertEqual(app.theme, "light")  # bright by default
        app.set_agent("Claude Code")
        n = len(app.shown)
        app.toggle_theme()
        self.assertEqual((app.theme, mgr.load_settings()["theme"]), ("dark", "dark"))
        self.assertEqual(len(app.shown), n)
        self.shot(app.root, "t11_dark")
        app.toggle_theme()
        self.assertEqual((app.theme, mgr.load_settings()["theme"]), ("light", "light"))
        self.shot(app.root, "t11_light")

    def test_12_no_italic_anywhere(self):
        app = self.app
        app.add_dialog()
        dlg = app.dialog
        menu = app.build_menu(app.shown[0])
        for page in ("링크로 설치", "새로 만들기"):
            dlg.show(page)
            for win in (app.root, dlg.win, menu):
                for w in walk(win):
                    try:
                        spec = w.cget("font")
                    except tk.TclError:
                        continue  # frames etc. have no font option
                    f = tkfont.Font(root=app.root, font=spec)
                    self.assertEqual(f.actual("slant"), "roman", f"{w} {spec}")
        self.assertEqual(app.f(11)[-1], "roman")
        dlg.close()

    def test_13_bundled_pretendard_is_used(self):
        fonts = Path(v.__file__).parent / "fonts"
        self.assertTrue((fonts / "Pretendard-Regular.otf").is_file() and (fonts / "Pretendard-Bold.otf").is_file())
        self.assertTrue((fonts / "LICENSE.txt").is_file())  # OFL: the license travels with the font files
        app = self.app
        self.assertEqual(app.family, "Pretendard")
        regular = tkfont.Font(root=app.root, family=app.family, size=10)
        self.assertEqual((regular.actual("family"), regular.actual("weight")), ("Pretendard", "normal"))
        self.assertEqual((app.name_font.actual("family"), app.name_font.actual("weight")), ("Pretendard", "bold"))

    def test_14_text_is_readable_on_its_background_in_both_themes(self):
        """WCAG contrast for every text/background pair actually on screen, including hover, selected and disabled."""
        app = self.app
        pack = self.tmp / "contrast-pack"
        for n in ("ca", "cb"):
            write_skill(pack / n)
        bad = []
        for _ in range(2):  # light, then dark
            app.set_agent("Claude Code")
            app.pick(None)
            bad += low_contrast(app, app.root, app.theme)
            app.pick(sorted({s["cat"] for s in app.shown})[0])  # a selected rail row
            bad += low_contrast(app, app.root, app.theme + " picked")
            for w in walk(app.root):  # hover states: header buttons, rows, rail
                if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2":
                    w.event_generate("<Enter>")
                    bad += low_contrast(app, w.master, app.theme + " hover")
                    w.event_generate("<Leave>")
            for s in (app.shown[0], next(s for s in app.data["Claude Code"] if not s["managed"])):
                bad += low_contrast(app, app.build_menu(s), app.theme + " menu")
            app.add_dialog()
            dlg = app.dialog
            bad += low_contrast(app, dlg.win, app.theme + " dialog, install disabled")
            dlg._show_found(mgr.discover(pack))
            bad += low_contrast(app, dlg.win, app.theme + " dialog, install enabled")
            dlg.show("새로 만들기")
            bad += low_contrast(app, dlg.win, app.theme + " dialog, new")
            dlg.close()
            app.toggle_theme()
        self.maxDiff = None
        self.assertEqual(bad, [])


def rgb(app, color):
    return [x / 65535 for x in app.root.winfo_rgb(color)]


def contrast(app, a, b):
    def lum(color):
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb(app, color)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def low_contrast(app, top, where):
    """-> 'where: widget text fg/bg ratio' for every pair below WCAG AA (4.5, or 3 for large or disabled text)."""
    out = []
    for w in walk(top):
        pairs = []
        try:
            font = tkfont.Font(root=app.root, font=w.cget("font"))
        except tk.TclError:
            continue  # no text on this widget
        large = font.actual("size") >= 18 or (font.actual("size") >= 14 and font.actual("weight") == "bold")
        need = 3.0 if large else 4.5
        if isinstance(w, tk.Menu):
            pairs = [
                ("fg", w.cget("fg"), w.cget("bg"), need),
                ("active", w.cget("activeforeground"), w.cget("activebackground"), need),
                ("disabled", w.cget("disabledforeground"), w.cget("bg"), 3.0),
            ]
        elif isinstance(w, (tk.Label, tk.Checkbutton, tk.Entry)):
            pairs = [("fg", w.cget("fg"), w.cget("bg"), need)]
            if isinstance(w, tk.Label) and str(w.cget("state")) == "disabled":  # a disabled button
                pairs = [("disabled", w.cget("disabledforeground"), w.cget("bg"), 3.0)]
            if isinstance(w, tk.Checkbutton):
                pairs.append(("check", w.cget("fg"), w.cget("selectcolor"), 3.0))
        for kind, fg, bg, limit in pairs:
            r = contrast(app, fg, bg)
            if r < limit:
                text = w.cget("text") if "text" in w.keys() else w.winfo_class()  # noqa: SIM118 - Tk widgets have keys() but no __contains__
                out.append(f"{where}: {str(text)[:20]!r} {kind} {fg} on {bg} = {r:.2f} < {limit}")
    return out


class PlatformTest(unittest.TestCase):
    """Behaviour that must be the same on Windows and macOS."""

    def test_directory_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            real = t / "real"
            real.mkdir()
            (real / "SKILL.md").write_text("x", encoding="utf-8")
            link = t / "link"
            plat.make_dir_link(link, real)
            self.assertTrue(mgr.is_link(link) and (link / "SKILL.md").is_file())
            plat.unlink_dir(link)  # removes only the link
            self.assertFalse(os.path.lexists(link))
            self.assertTrue((real / "SKILL.md").is_file())
            with self.assertRaises(OSError):
                plat.unlink_dir(real)  # a real folder is never removed this way

    def test_second_copy_of_the_app_is_refused(self):
        name = f"skill-catalog-test-{os.getpid()}"
        self.assertFalse(plat.already_running(name))
        other = subprocess.run(
            ["python", "-c", f"import platform_support as p; print(p.already_running({name!r}))"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        )
        self.assertEqual(other.stdout.strip(), "True")

    def test_right_click_sequences_match_the_platform(self):
        self.assertEqual(plat.RIGHT_CLICK[0], "<Button-2>" if plat.MAC else "<Button-3>")

    def test_tray_menu_builds_and_only_known_commands_are_accepted(self):
        sent = []
        menu = v.tray_menu(sent.append)
        for item in menu.items:
            if item.text != "":
                item(None)  # pystray calls the action with (icon, item); the lambdas ignore them
        self.assertEqual(set(sent), v.TRAY_COMMANDS)
        self.assertTrue(all(c in v.TRAY_COMMANDS for c in sent))

    def test_tray_child_output_is_filtered(self):
        class Fake:
            stdout = ["toggle\n", "rm -rf /\n", "quit\n", "__class__\n"]

        class Stub:
            tray_proc = Fake()
            events = __import__("queue").Queue()

        v.App._read_tray(Stub())  # type: ignore[arg-type]
        got = []
        while not Stub.events.empty():
            got.append(Stub.events.get_nowait())
        self.assertEqual(got, ["toggle", "quit"])

    def test_font_scale_keeps_sizes_comparable(self):
        self.assertAlmostEqual(plat.FONT_SCALE, 4 / 3 if plat.MAC else 1.0)


class CategoryTest(unittest.TestCase):
    def test_every_skill_is_in_exactly_one_category(self):
        listed = [s for c in v.CATALOG for s in c["skills"]]
        dupes = sorted({s for s in listed if listed.count(s) > 1})
        self.assertEqual(dupes, [], "a skill is listed under two categories")

    def test_every_described_skill_has_a_category(self):
        missing = sorted(n for n in v.NOTES if n not in v.CATEGORY_OF)
        self.assertEqual(missing, [], "add these to skill_categories.json")

    def test_categories_colors_and_descriptions_line_up(self):
        names = [c["name"] for c in v.CATALOG]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(v.CAT_COLORS), set(names) | {v.OTHER})
        self.assertTrue(all(c["about"] for c in v.CATALOG))

    def test_listed_skills_ignore_keyword_guesses(self):
        # 'review' would otherwise pull ui-audit into 리뷰·품질; the list wins, with or without a plugin prefix
        self.assertEqual(v.categorize("ui-audit", "audit the UI"), "UI·디자인")
        self.assertEqual(v.categorize("superpowers:requesting-code-review", ""), "리뷰·품질")
        self.assertEqual(v.categorize("zz-helper", "Review pull requests for bugs"), "리뷰·품질")  # unknown: keywords
        self.assertEqual(v.categorize("zzz", "nothing matches here"), v.OTHER)


class DiscoveryTest(unittest.TestCase):
    """What happens on someone else's PC: other layouts, missing agents, broken files. No Tk needed."""

    def test_odd_skill_files_do_not_break_the_list(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "plain").mkdir()
            (root / "plain/SKILL.md").write_text("no front matter at all\n", encoding="utf-8")
            (root / "binary").mkdir()
            (root / "binary/SKILL.md").write_bytes(b"\xff\xfe\x00garbage")
            (root / "multiline").mkdir()
            (root / "multiline/SKILL.md").write_text("---\nname: ml\ndescription: >\n  first line\n  second line\n---\n", encoding="utf-8")
            got = {s["name"]: s for s in v.scan(root)}
            self.assertEqual(set(got), {"plain", "binary", "ml"})
            self.assertEqual(got["ml"]["desc"], "first line second line")

    def test_missing_agent_folders_are_just_empty(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            dirs = {"Claude Code": t / "a", "Codex": t / "b", "Antigravity": t / "c"}
            with mock.patch.object(mgr, "AGENT_DIRS", dirs), mock.patch.object(mgr, "CLAUDE_HOME", t / "home"):
                for agent in dirs:
                    # only a built-in skill folder (not under the user's control) can exist on such a PC
                    self.assertTrue(all(s["origin"] == "내장" for s in v.collect(agent)))

    def test_plugin_roots_tolerate_missing_or_odd_files(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(mgr, "CLAUDE_HOME", Path(t)):
            home = Path(t)
            self.assertEqual(v.claude_plugin_roots(), [])  # no plugins folder at all
            (home / "plugins").mkdir()
            (home / "plugins/installed_plugins.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(v.claude_plugin_roots(), [])
            odd = {"plugins": {"a@m": [{}], "b@m": [], "c@m": [{"installPath": str(home / "c")}]}}
            (home / "plugins/installed_plugins.json").write_text(json.dumps(odd), encoding="utf-8")
            (home / "settings.json").write_text(json.dumps({"enabledPlugins": {"a@m": True, "b@m": True, "c@m": True}}), encoding="utf-8")
            self.assertEqual(v.claude_plugin_roots(), [(home / "c" / "skills", "c:")])

    def test_agent_homes_follow_env_vars(self):
        out = subprocess.run(
            ["python", "-c", "import skill_manager as m; print(m.AGENT_DIRS['Claude Code']); print(m.AGENT_DIRS['Codex'])"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            env={**os.environ, "CLAUDE_CONFIG_DIR": "D:/cc", "CODEX_HOME": "D:/cx"},
        ).stdout.split()
        self.assertEqual([Path(x) for x in out], [Path("D:/cc/skills"), Path("D:/cx/skills")])


if __name__ == "__main__":
    unittest.main()
