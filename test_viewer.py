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

import install
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


def open_menu(app, s, x, y):
    """Open a skill's ⋯ menu that stays open while a test inspects it. Click-away closing listens for the menu
    losing focus, which other test windows grabbing focus would trigger at random."""
    app.row_menu(s, x, y)
    app.popup.win.unbind("<FocusOut>")
    app.root.update()


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
        # so the Claude tab has several categories and a read-only skill even on a clean CI machine without plugins
        write_skill(t / "claude/tdd")
        write_skill(t / "claude/synced/app/grilling")  # nested = synced by the desktop app = view only
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
        app._last_toggle = 0.0  # tests toggle faster than a double-click
        app.events.put("toggle")
        app._pump()
        self.assertTrue(not app.visible and app.root.state() == "withdrawn")
        app._last_toggle = 0.0  # tests toggle faster than a double-click
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
        app._last_toggle = 0.0  # tests toggle faster than a double-click
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_01c_window_is_not_always_on_top(self):
        app = self.app
        app.show()  # opening from the tray pops to the front once, then must not stay topmost
        app.root.update()
        self.assertFalse(app.root.attributes("-topmost"))
        app.add_dialog()
        app.dialog.win.update()
        self.assertFalse(app.dialog.win.attributes("-topmost"))
        app.dialog.close()

    def test_app_name_is_the_same_everywhere(self):
        app = self.app
        self.assertEqual(app.root.title(), install.APP_NAME)  # window title = Start menu / Spotlight name
        self.assertIn(install.APP_NAME, texts(app.root))  # header

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

    def test_01c_tray_double_click_does_not_undo_itself(self):
        app = self.app
        app.hide()
        app._last_toggle = 0.0
        app.events.put("toggle")  # a double-click on the tray icon sends two clicks at once
        app.events.put("toggle")
        app._pump()
        self.assertTrue(app.visible and app.root.state() == "normal")

    def test_03c_category_click_reuses_built_rows(self):
        app = self.app
        app.set_agent("Claude Code")
        app.pick(None)
        cats = sorted({s["cat"] for s in app.shown})
        self.assertGreater(len(cats), 1)
        built = app.sections[cats[0]]
        app.pick(cats[1])
        self.assertEqual(built.winfo_manager(), "")  # hidden, not destroyed
        app.pick(cats[0])
        self.assertIs(app.sections[cats[0]], built)  # same widgets as before, nothing rebuilt
        self.assertEqual(built.winfo_manager(), "pack")
        self.assertTrue(all(s["cat"] == cats[0] for s in app.shown))
        app.pick(None)  # everything shown again: the hidden groups come back in category order
        order = [c for c in [n for n, _ in v.CATEGORIES] + [v.OTHER] if c in app.sections]
        self.assertEqual(app.body.pack_slaves(), [app.sections[c] for c in order])
        app.pick(cats[0])
        app.query.set("zzzz-no-match")  # a search changes the rows, so the groups must be rebuilt
        self.assertIsNot(app.sections.get(cats[0]), built)
        self.assertFalse(built.winfo_exists())
        app.query.set("")
        app.pick(None)

    def test_03d_load_redraws_only_when_something_changed(self):
        app = self.app
        with mock.patch.object(app, "render") as render:
            app.load()
            render.assert_not_called()  # same skills as on screen: leave the list alone
            app.data["Codex"] = [{"name": "stale"}]
            app.load()
            render.assert_called_once()
        app.render()

    def test_03e_star_list_is_per_agent_saved_and_always_in_the_rail(self):
        app = self.app
        app.set_agent("Claude Code")
        app.query.set("")
        names = texts(app.rail)
        self.assertEqual((names[1], names[3]), (v.STAR, "전체"))  # the star entry sits above "전체", and shows at 0
        self.assertEqual(names[2], "0")
        gs = find(app, "Claude Code", "gitskill")
        self.assertIn("★ 자주 쓰는 스킬에 추가", [label for label, _, _ in app.menu_items(gs)])
        icon = app.icons["gitskill"][0]
        self.assertEqual(icon.cget("text"), "☆")  # every row has the icon, hollow until starred
        before = dict(app.sections)
        app.toggle_pin(gs)
        self.assertEqual(icon.cget("text"), "★")
        self.assertEqual(app.sections, before)  # the icon was repainted in place; no group was rebuilt
        self.assertEqual(mgr.load_settings()["pins"]["Claude Code"], ["gitskill"])  # saved
        self.assertEqual(v.load_pins()["Claude Code"], {"gitskill"})
        self.assertEqual(app.pins["Codex"], set())  # another agent's list is separate
        self.assertIn("★ 자주 쓰는 스킬에서 해제", [label for label, _, _ in app.menu_items(gs)])
        app.pick(v.STAR)
        self.assertEqual([s["name"] for s in app.shown], ["gitskill"])
        self.assertIn(gs["cat"], texts(app.body))  # the star list names each skill's own category
        app.toggle_pin(gs)
        self.assertEqual((app.shown, app.cat), ([], v.STAR))  # stays on the star list; nothing starred
        self.assertIn("아직 지정한 스킬이 없습니다", "".join(texts(app.body)))
        self.assertEqual(texts(app.rail)[1], v.STAR)  # the entry stays at 0
        app.set_agent("Claude Code")

    def test_03g_settings_dialog_chooses_which_agent_tabs_exist(self):
        app = self.app
        app.set_agent("Codex")
        app.settings_dialog()
        app.root.update()
        d = app.dialog
        self.assertEqual(set(d.vars), set(v.AGENTS))
        d.vars["Codex"].set(False)  # untick: the tab and its list go away, the panel falls back to the first tab
        app.root.update()
        try:
            self.assertEqual(app.agents, ["Claude Code", "Antigravity"])
            self.assertEqual(mgr.load_settings()["agents"], ["Claude Code", "Antigravity"])
            self.assertEqual(app.agent.get(), "Claude Code")
            self.assertEqual([t for t in texts(app.tabs) if "Codex" in t], [])
            self.assertNotIn("Codex", app.data)
            self.assertTrue(d.win.winfo_exists())  # the dialog stays open while the panel is rebuilt
            self.assertEqual([a for a, var in app.dialog.vars.items() if var.get()], app.agents)
            self.shot(d.win, "t3g_settings")
            for a in ("Claude Code", "Antigravity"):  # the last way to an empty panel is refused
                d.vars[a].set(False)
            self.assertEqual(len(app.agents), 1)
            self.assertIn("하나 이상", d.status.cget("text"))
            self.assertTrue(any(var.get() for var in d.vars.values()))
            app.add_dialog()  # the add dialog only offers the shown agents
            self.assertEqual(list(app.dialog.agents), app.agents)
            app.dialog.close()
        finally:
            app.settings_dialog()
            for var in app.dialog.vars.values():
                var.set(True)
            app.dialog.close()
            app.root.update()
        self.assertEqual(app.agents, list(v.AGENTS))
        self.assertIn("Codex", app.data)
        app.set_agent("Claude Code")

    @unittest.skipUnless(plat.WIN, "the one synthetic click is only checked on Windows")
    def test_03f_clicking_the_star_icon_stars_the_skill_without_copying_its_name(self):
        app = self.app
        app.set_agent("Claude Code")
        icon = app.icons["gitskill"][0]
        app.copy("sentinel")
        app.root.update()  # a widget that isn't mapped yet ignores events
        icon.event_generate("<Button-1>", x=2, y=2)
        self.assertEqual((icon.cget("text"), app.root.clipboard_get()), ("★", "sentinel"))
        self.assertIn("gitskill", app.pins["Claude Code"])
        icon.event_generate("<Button-1>", x=2, y=2)  # a second click takes it back
        self.assertEqual(icon.cget("text"), "☆")

    def test_04_menu_entries(self):
        app = self.app

        def labels(s):
            return [label for label, _, kind in app.menu_items(s) if kind != "-"]

        legacy = labels(find(app, "Codex", "legacy"))
        self.assertEqual(legacy[0], "상세 보기")
        self.assertIn("삭제…", legacy)
        self.assertIn("SKILL.md 열어서 수정", legacy)
        self.assertIn("이 스킬의 저장소 업데이트 확인", labels(find(app, "Claude Code", "gitskill")))
        ro = next(s for s in app.data["Claude Code"] if not s["managed"])
        self.assertNotIn("삭제…", labels(ro))  # plugin / app-synced skills can't be deleted from here

        # the themed popup: one at a time, runs the picked action, closes on pick and on Esc
        open_menu(app, find(app, "Codex", "legacy"), 200, 200)
        app.root.update()
        first = app.popup
        self.assertEqual([r.cget("text") for r in first.rows][0], "상세 보기")
        self.shot(first.win, "t4_popup")
        open_menu(app, find(app, "Codex", "legacy"), 220, 220)
        app.root.update()  # a click only reaches a window that is on screen
        self.assertFalse(first.win.winfo_exists())
        next(r for r in app.popup.rows if r.cget("text") == "이름 복사").event_generate("<Button-1>")
        app.root.update()
        self.assertEqual(app.root.clipboard_get(), "legacy")
        self.assertFalse(app.popup.win.winfo_exists())
        open_menu(app, find(app, "Codex", "legacy"), 200, 200)
        app.root.update()
        app.popup.win.event_generate("<Escape>")
        app.root.update()
        self.assertFalse(app.popup.win.winfo_exists())

    def test_04b_detail_page(self):
        app = self.app
        app.detail(find(app, "Claude Code", "gitskill"))
        app.root.update()
        d = app.detail_win
        shown = texts(d.win)
        for expected in ("gitskill", "저장소", "버전", "설치된 에이전트", "위치", "SKILL.md", "삭제…", "업데이트 확인"):
            self.assertTrue(any(expected in t for t in shown), expected)
        self.assertTrue(any(t.startswith("git · ") for t in shown))
        self.assertIn("description: does a thing", d.text.get("1.0", "end"))
        self.assertEqual(str(d.text.cget("state")), "disabled")  # a viewer, not an editor
        with mock.patch.object(v.plat, "open_folder") as opened:
            next(w for w in walk(d.win) if isinstance(w, tk.Label) and w.cget("text") == "폴더 열기").event_generate("<Button-1>")
        opened.assert_called_once()
        buttons = [w for w in walk(d.win) if isinstance(w, tk.Label) and str(w.cget("cursor")) == "hand2"]
        for b in buttons:  # nothing in the action rows is cut off
            self.assertLessEqual(b.winfo_reqwidth(), b.winfo_width() + 1, b.cget("text"))
            self.assertLessEqual(b.winfo_x() + b.winfo_width(), b.master.winfo_width(), b.cget("text"))
        self.shot(d.win, "t4b_detail")
        ro = next(s for s in app.data["Claude Code"] if not s["managed"])
        app.detail(ro)
        app.root.update()
        self.assertFalse(d.win.winfo_exists())  # one detail window at a time
        self.assertNotIn("삭제…", texts(app.detail_win.win))
        app.detail_win.close()

    def test_04c_themed_checkbox(self):
        app = self.app
        holder = tk.Frame(app.root)
        holder.pack()
        var = tk.BooleanVar(app.root, value=False)
        cb = app._check(holder, "옵션", var)
        cb.pack()
        app.root.update()

        def fill():
            return cb.box.itemcget(cb.box.find_all()[0], "fill")

        self.assertEqual((fill(), len(cb.box.find_all())), (app.c["surface"], 1))
        cb.box.event_generate("<Button-1>")
        app.root.update()
        self.assertTrue(var.get())
        self.assertEqual((fill(), len(cb.box.find_all())), (app.c["accent"], 2))  # filled box + tick
        cb.focus_force()  # keys go to the focused widget; another test's window may still hold focus
        app.root.update()
        cb.event_generate("<space>")  # keyboard works too
        app.root.update()
        self.assertFalse(var.get())
        holder.destroy()
        var.set(True)  # the trace went away with the widget, so nothing redraws a dead canvas
        for pal in (v.LIGHT, v.DARK):  # a control's outline and tick need 3:1 (WCAG 1.4.11)
            self.assertGreaterEqual(contrast(app, pal["muted"], pal["bg"]), 3)
            self.assertGreaterEqual(contrast(app, pal["accent"], pal["bg"]), 3)
            self.assertGreaterEqual(contrast(app, pal["on_accent"], pal["accent"]), 3)

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

        def check(win):
            for w in walk(win):
                try:
                    spec = w.cget("font")
                except tk.TclError:
                    continue  # frames etc. have no font option
                f = tkfont.Font(root=app.root, font=spec)
                self.assertEqual(f.actual("slant"), "roman", f"{w} {spec}")

        app.detail(app.shown[0])
        open_menu(app, app.shown[0], 200, 200)
        app.root.update()
        check(app.popup.win)
        app.close_popup()
        for page in ("링크로 설치", "새로 만들기"):
            dlg.show(page)
            for win in (app.root, dlg.win, app.detail_win.win):
                check(win)
        app.detail_win.close()
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
                open_menu(app, s, 200, 200)
                app.root.update()
                bad += low_contrast(app, app.popup.win, app.theme + " menu")
                for r in app.popup.rows:
                    r.event_generate("<Enter>")
                    bad += low_contrast(app, r, app.theme + " menu hover")
                    r.event_generate("<Leave>")
                app.close_popup()
                app.detail(s)
                app.root.update()
                bad += low_contrast(app, app.detail_win.win, app.theme + " detail")
                app.detail_win.close()
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
        elif isinstance(w, (tk.Label, tk.Checkbutton, tk.Entry, tk.Text)):
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

    def test_open_folder_only_reveals_and_never_launches(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(plat.subprocess, "Popen") as popen:
            app_like = Path(t) / "evil.app"  # a skill folder name a downloaded repo could use
            app_like.mkdir()
            plat.open_folder(app_like)
            cmd = popen.call_args[0][0]
            expected = ["explorer"] if plat.WIN else ["open", "-R"] if plat.MAC else ["xdg-open"]
            self.assertEqual(cmd[:-1], expected)
            popen.reset_mock()
            plat.open_folder(Path(t) / "missing")  # not a folder: nothing is started
            popen.assert_not_called()


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


class TildeTest(unittest.TestCase):
    def test_home_paths_are_shortened(self):
        home = Path.home()
        self.assertEqual(v.tilde(home / "x" / "y"), "~" + str(Path("/x/y")))
        if plat.WIN:  # Windows paths ignore case
            self.assertEqual(v.tilde(Path(str(home).upper()) / "z"), "~" + str(Path("/z")))
        outside = Path(home.anchor) / "elsewhere-for-test"
        self.assertEqual(v.tilde(outside), str(outside))


class DiscoveryTest(unittest.TestCase):
    """What happens on someone else's PC: other layouts, missing agents, broken files. No Tk needed."""

    def test_linked_skills_are_listed_and_link_loops_do_not_hang(self):
        # macOS CI caught this: installed skills are symlinks there, and os.walk does not enter them by default
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            source = t / "source/alpha"
            write_skill(source)
            agent = t / "agent"
            agent.mkdir()
            plat.make_dir_link(agent / "alpha", source)
            plat.make_dir_link(agent / "loop", agent)  # points back up the tree
            try:
                self.assertEqual([s["name"] for s in v.scan(agent)], ["alpha"])
            finally:
                plat.unlink_dir(agent / "alpha")
                plat.unlink_dir(agent / "loop")

    def test_pins_tolerate_odd_settings(self):
        empty = {a: set() for a in v.AGENTS}
        for odd in (None, "junk", [], {"Codex": "abc"}):
            with mock.patch.object(mgr, "load_settings", return_value={"pins": odd}):
                self.assertEqual(v.load_pins(), empty)
        with mock.patch.object(mgr, "load_settings", return_value={"pins": {"Codex": [1, "x"]}}):
            self.assertEqual(v.load_pins()["Codex"], {"x"})

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
