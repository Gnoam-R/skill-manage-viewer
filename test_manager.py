"""python -m unittest test_manager -v   (needs git; works in a temp dir, never touches the real ~/.claude etc.)"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import platform_support as plat
import skill_manager as m

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def sh(cwd: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True)


def write_skill(d: Path, desc: str = "does a thing") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


class ParseLink(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(m.parse_link("mattpocock/skills"), ("https://github.com/mattpocock/skills", None, None))
        self.assertEqual(m.parse_link("o/r/skills/tdd"), ("https://github.com/o/r", None, "skills/tdd"))
        self.assertEqual(m.parse_link("https://github.com/o/r.git"), ("https://github.com/o/r", None, None))
        self.assertEqual(m.parse_link("https://github.com/o/r/tree/main/skills/tdd/"), ("https://github.com/o/r", "main", "skills/tdd"))
        self.assertEqual(
            m.parse_link("https://github.com/o/r/blob/v1/skills/tdd/SKILL.md#x"), ("https://github.com/o/r", "v1", "skills/tdd")
        )
        self.assertEqual(m.parse_link("https://gitlab.example.com/g/r/-/tree/dev/s"), ("https://gitlab.example.com/g/r", "dev", "s"))

    def test_rejected_forms(self):
        for bad in (
            "",
            "file:///c:/x",
            "ext::sh -c id",
            "git@github.com:o/r.git",
            "http://github.com/o/r",
            "https://user:pw@github.com/o/r",
            "https://github.com/o/r/tree/main/../../x",
            "https://github.com/o/r/tree/-x/p",
            "C:\\skills\\x",
            "../x/y",
            "justone",
        ):
            with self.assertRaises(m.ManagerError, msg=bad):
                m.parse_link(bad)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self.tmp = Path(self._tmp.name).resolve()
        m.STORE = t / "store"
        m.AGENT_DIRS = {"Claude Code": t / "claude", "Codex": t / "codex", "Antigravity": t / "agy"}
        m.UPDATER = t / "updater.ps1"
        m.version.cache_clear()
        # an "upstream" with two skills, cloned shallow like fetch() does
        self.origin = t / "origin.git"
        sh(t, "init", "-q", "--bare", "--initial-branch=main", str(self.origin))
        self.work = t / "work"
        sh(t, "clone", "-q", self.origin.as_uri(), str(self.work))
        write_skill(self.work / "skills/alpha")
        write_skill(self.work / "skills/beta")
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", "init")
        sh(self.work, "push", "-q", "origin", "HEAD:main")
        self.clone = m.STORE / "sources" / "origin"
        self.clone.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.origin.as_uri(), str(self.clone)], check=True, capture_output=True)

    def tearDown(self):
        # junctions must be unlinked first or TemporaryDirectory would walk into the clone through them
        for d in m.AGENT_DIRS.values():
            for p in d.iterdir() if d.is_dir() else []:
                if m.is_link(p):
                    plat.unlink_dir(p)
        self._tmp.cleanup()

    def test_install_update_relink_remove(self):
        skills = m.discover(self.clone)
        self.assertEqual([s.name for s in skills], ["alpha", "beta"])
        res = m.install(skills, ["Claude Code", "Codex"])
        self.assertEqual({r.status for r in res}, {"installed"})
        link = m.AGENT_DIRS["Claude Code"] / "alpha"
        self.assertTrue((link / "SKILL.md").is_file())
        self.assertEqual(m.origin(link), ("git", self.clone.resolve()))
        self.assertEqual(m.parse(link / "SKILL.md")["desc"], "does a thing")

        # installing again is a no-op
        self.assertEqual({r.status for r in m.install(skills, ["Claude Code"])}, {"exists"})

        # nothing new upstream yet
        self.assertFalse(m.has_update(self.clone))

        # upstream edits alpha and moves beta into a subfolder
        write_skill(self.work / "skills/alpha", "does a better thing")
        (self.work / "skills/sub").mkdir()
        sh(self.work, "mv", "skills/beta", "skills/sub/beta")
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", "move")
        sh(self.work, "push", "-q", "origin", "HEAD:main")
        self.assertTrue(m.has_update(self.clone))
        out = m.update(self.clone)
        self.assertNotEqual(out["old"], out["new"])
        self.assertEqual(out["gone"], [])
        self.assertEqual(m.parse(link / "SKILL.md")["desc"], "does a better thing")
        for agent in ("Claude Code", "Codex"):  # beta's links were re-pointed in both agents
            self.assertTrue((m.AGENT_DIRS[agent] / "beta/SKILL.md").is_file(), agent)
        self.assertFalse(m.has_update(self.clone))

        # remove from one agent: link goes, the shared source and the other agent stay
        self.assertIsNone(m.remove("Claude Code", "alpha"))
        self.assertFalse(link.exists() and m.is_link(link))
        self.assertTrue((m.AGENT_DIRS["Codex"] / "alpha/SKILL.md").is_file())
        self.assertTrue((self.clone / "skills/alpha/SKILL.md").is_file())

    def test_upstream_deleted_skill_is_reported(self):
        m.install(m.discover(self.clone), ["Codex"])
        sh(self.work, "rm", "-rq", "skills/beta")
        sh(self.work, "commit", "-qm", "rm")
        sh(self.work, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(m.update(self.clone)["gone"], ["beta"])
        self.assertFalse(os.path.lexists(m.AGENT_DIRS["Codex"] / "beta"))

    def test_conflict_replace_and_trash(self):
        mine = m.AGENT_DIRS["Codex"] / "alpha"
        write_skill(mine, "my own alpha")  # a real folder the user made
        skills = m.discover(self.clone)
        res = m.install(skills[:1], ["Codex"])
        self.assertEqual(res[0].status, "conflict")
        self.assertEqual(m.parse(mine / "SKILL.md")["desc"], "my own alpha")  # untouched
        res = m.install(skills[:1], ["Codex"], replace=True)
        self.assertEqual(res[0].status, "replaced")
        backups = list((m.STORE / "trash").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(m.parse(backups[0] / "SKILL.md")["desc"], "my own alpha")

    def test_remove_real_folder_goes_to_trash(self):
        write_skill(m.AGENT_DIRS["Claude Code"] / "mine")
        moved = m.remove("Claude Code", "mine")
        self.assertIsNotNone(moved)
        self.assertTrue((moved / "SKILL.md").is_file())
        self.assertFalse((m.AGENT_DIRS["Claude Code"] / "mine").exists())
        with self.assertRaises(m.ManagerError):
            m.remove("Claude Code", "mine")
        with self.assertRaises(m.ManagerError):
            m.remove("Claude Code", "..")

    def test_create_own_skill_and_remove_everywhere(self):
        d, res = m.create("my-skill", 'say "hi" please', ["Claude Code", "Codex"])
        self.assertEqual({r.status for r in res}, {"installed"})
        self.assertEqual(m.parse(d / "SKILL.md")["desc"], "say 'hi' please")
        link = m.AGENT_DIRS["Codex"] / "my-skill"
        self.assertEqual(m.origin(link), ("own", None))
        with self.assertRaises(m.ManagerError):
            m.create("my-skill", "", ["Claude Code"])
        with self.assertRaises(m.ManagerError):
            m.create("bad name", "", ["Claude Code"])
        m.remove("Claude Code", "my-skill")
        self.assertTrue(d.exists())  # Codex still uses it
        m.remove("Codex", "my-skill")
        self.assertFalse(d.exists())  # last user gone: source moved to trash, not deleted
        self.assertTrue(any("my-skill" in p.name for p in (m.STORE / "trash").iterdir()))

    def test_create_refuses_when_name_taken_everywhere(self):
        write_skill(m.AGENT_DIRS["Codex"] / "taken")
        with self.assertRaises(m.ManagerError):
            m.create("taken", "", ["Codex"])
        self.assertFalse((m.STORE / "skills/taken").exists())  # no orphan template left behind

    def test_notes_roundtrip(self):
        m.set_note("alpha", "  내 설명 ")
        self.assertEqual(m.load_notes(), {"alpha": "내 설명"})
        m.set_note("alpha", "")
        self.assertEqual(m.load_notes(), {})

    def test_forget_in_updater(self):
        m.UPDATER.write_text("$repo = 'x'\n$names  = 'a','b','c'\n$targets = @('t')\n", encoding="utf-8")
        write_skill(m.AGENT_DIRS["Codex"] / "b")
        m.remove("Codex", "b")
        self.assertIn("$names  = 'a','c'\n", m.UPDATER.read_text(encoding="utf-8"))
        self.assertFalse(m.forget_in_updater("zzz"))


if __name__ == "__main__":
    unittest.main()
