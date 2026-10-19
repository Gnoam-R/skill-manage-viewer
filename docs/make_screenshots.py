"""Regenerate the README screenshots in docs/images/ (Windows only: uses the screen grabber).

    python docs/make_screenshots.py

Runs the real app against a made-up set of public skills in a temp folder, so no one's own skills, paths or
settings end up in the pictures. Nothing outside the temp folder is written.
"""

import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_support as plat  # noqa: E402
import skill_manager as mgr  # noqa: E402
import skill_viewer as v  # noqa: E402

OUT = ROOT / "docs" / "images"
MANUAL = {"grill-me", "grill-with-docs", "improve-codebase-architecture", "ui-audit"}
GIT_SKILLS = ["grilling", "grill-me", "grill-with-docs", "domain-modeling", "codebase-design", "improve-codebase-architecture", "tdd"]
LOCAL_SKILLS = ["ui-polish", "ui-audit", "web-design-guidelines", "pdf", "docx", "xlsx", "pptx", "deep-research", "built-in-browser"]
SYNCED_SKILLS = ["skill-creator", "canvas-design", "theme-factory"]
PLUGIN_SKILLS = ["brainstorming", "systematic-debugging", "writing-plans", "verification-before-completion",
                 "requesting-code-review", "using-git-worktrees", "executing-plans"]


def write_skill(d: Path, desc: str | None = None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    extra = "disable-model-invocation: true\n" if d.name in MANUAL else ""
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {desc or 'Demo skill ' + d.name}\n{extra}---\n",
                                encoding="utf-8")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=demo", "-c", "user.email=demo@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def demo_world(t: Path) -> Path:
    """Agent folders full of public example skills. -> the git repo some of them come from."""
    agents = {"Claude Code": t / "claude/skills", "Codex": t / "codex/skills", "Antigravity": t / "gemini/skills"}
    mgr.AGENT_DIRS.clear()
    mgr.AGENT_DIRS.update(agents)
    repo = t / "store/sources/github.com__example__skills"
    for n in GIT_SKILLS:
        write_skill(repo / "skills" / n)
    git(t, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "skills")
    mgr.install([repo / "skills" / n for n in GIT_SKILLS], ["Claude Code"])
    mgr.install([repo / "skills" / n for n in ("grilling", "tdd", "domain-modeling")], ["Codex", "Antigravity"])
    for n in LOCAL_SKILLS:
        write_skill(agents["Claude Code"] / n)
    for n in SYNCED_SKILLS:
        write_skill(agents["Claude Code"] / "synced/app" / n)
    plugin = t / "claude/plugins/cache/demo/superpowers/5.0.0"
    for n in PLUGIN_SKILLS:
        write_skill(plugin / "skills" / n)
    (t / "claude/plugins").mkdir(parents=True, exist_ok=True)
    (t / "claude/plugins/installed_plugins.json").write_text(
        json.dumps({"plugins": {"superpowers@demo": [{"installPath": str(plugin)}]}}), encoding="utf-8")
    (t / "claude/settings.json").write_text(json.dumps({"enabledPlugins": {"superpowers@demo": True}}), encoding="utf-8")
    for n in ("skill-installer", "openai-docs"):
        write_skill(agents["Codex"] / ".system" / n)
    for n in ("ui-polish", "pdf"):
        write_skill(agents["Codex"] / n)
    mgr.create("meeting-notes", "Summarize meeting notes into a short report", ["Claude Code", "Codex"])
    mgr.set_note("meeting-notes", "회의록을 요약 보고서로 정리 (직접 만든 스킬 예시)")
    return repo


def bounds(win) -> tuple[int, int, int, int]:
    """Window rectangle including the title bar, as Windows draws it."""
    class RECT(ctypes.Structure):
        _fields_ = [(n, ctypes.c_long) for n in ("left", "top", "right", "bottom")]
    r = RECT()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))  # DWMWA_EXTENDED_FRAME_BOUNDS
    return r.left, r.top, r.right, r.bottom


def shot(win, name: str) -> None:
    from PIL import ImageGrab
    for _ in range(3):
        win.update()
        time.sleep(0.4)  # let the window finish fading in
    ImageGrab.grab(bbox=bounds(win)).save(OUT / f"{name}.png", optimize=True)
    print("saved", OUT / f"{name}.png")


def main() -> None:
    if not plat.WIN:
        sys.exit("Windows only")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        with mock.patch.object(mgr, "STORE", t / "store"), mock.patch.object(mgr, "CLAUDE_HOME", t / "claude"), \
                mock.patch.object(mgr, "UPDATER", t / "none.ps1"), mock.patch.object(mgr, "open_in_editor"), \
                mock.patch.object(mgr, "AGENT_DIRS", dict(mgr.AGENT_DIRS)), mock.patch.object(v, "HOME", t):
            repo = demo_world(t)
            app = v.App(with_tray=False)
            app.load()
            shot(app.root, "main")

            app.updatable = {repo.resolve()}
            app.pick("기획·설계")
            shot(app.root, "category")
            app.updatable = set()

            app.pick(None)
            app.query.set("검증")
            shot(app.root, "search")
            app.query.set("")

            app.toggle_theme()
            shot(app.root, "dark")
            app.toggle_theme()

            app.add_dialog()
            dlg = app.dialog
            dlg.link.set("example/skills")
            dlg._show_found(mgr.discover(repo))
            for d in list(dlg.picks)[:3]:
                dlg.picks[d].set(True)
            shot(dlg.win, "add-link")
            dlg.show("새로 만들기")
            dlg.name.set("release-notes")
            dlg.desc.set("Write release notes from merged pull requests")
            shot(dlg.win, "add-new")
            dlg.close()

            app.set_agent("Codex")
            shot(app.root, "codex")
            app.quit()
            for d in mgr.AGENT_DIRS.values():  # unlink before the temp folder is removed
                for p in (d.iterdir() if d.is_dir() else []):
                    if mgr.is_link(p):
                        plat.unlink_dir(p)


if __name__ == "__main__":
    main()
