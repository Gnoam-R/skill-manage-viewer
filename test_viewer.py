"""python -m unittest test_viewer -v

Drives the panel in-process (no synthetic mouse/keyboard input) against temp agent folders, so the real
~/.claude, ~/.codex and ~/.gemini are only read for plugin/built-in skills and never written.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import ImageGrab

import skill_manager as mgr
import skill_viewer as v

SHOTS = Path(tempfile.gettempdir())


def write_skill(d: Path, desc: str = "does a thing") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


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
        cls.app = v.App(with_tray=True)
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.quit()
        for d in mgr.AGENT_DIRS.values():  # unlink junctions first so cleanup can't walk into a clone through them
            for p in d.iterdir() if d.is_dir() else []:
                if mgr.is_link(p):
                    os.rmdir(p)
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
        ImageGrab.grab(bbox=box).save(SHOTS / f"{name}.png")
        return box

    def rows(self):
        return sum(1 for w in self.app.body.winfo_children() if w.cget("cursor") == "hand2")

    # ---- window, tray, list ---------------------------------------------------------------
    def test_01_tray_corner_and_toggle(self):
        app = self.app
        self.assertTrue(app.tray is not None and app.tray.HAS_MENU)
        left, top, right, bottom = v.work_area()
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

    def test_02_origin_labels(self):
        legacy = find(self.app, "Codex", "legacy")
        self.assertEqual((legacy["origin"], legacy["managed"], legacy["repo"]), ("로컬", True, None))
        g = find(self.app, "Claude Code", "gitskill")
        self.assertTrue(g["origin"].startswith("git · ") and g["repo"] == self.repo.resolve() and g["managed"])

    def test_03_tabs_search_fold(self):
        app = self.app
        app.set_agent("Claude Code")
        app.query.set("gitskill")
        app.root.update()
        self.assertEqual(self.rows(), 2)  # one category header + one row
        app.query.set("zzzz-no-match")
        app.root.update()
        self.assertEqual(self.rows(), 0)
        app.query.set("로컬")
        app.root.update()  # origin text is searchable too
        app.query.set("")
        before = self.rows()
        app.toggle_all()
        app.root.update()
        self.assertLess(self.rows(), before)
        app.toggle_all()
        app.root.update()
        self.assertEqual(self.rows(), before)
        self.shot(app.root, "t3_list")
        app.copy("grilling")
        self.assertEqual(app.root.clipboard_get(), "grilling")

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
        labels = [
            w.cget("text")
            for f in app.body.winfo_children()
            for t in f.winfo_children()
            for w in t.winfo_children()
            if isinstance(w, v.tk.Label)
        ]
        self.assertIn("업데이트 있음", labels)
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


if __name__ == "__main__":
    unittest.main()
