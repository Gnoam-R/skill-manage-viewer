"""Install, update, remove and create agent skills. Pure logic (no Tk) so it can be tested on its own.

Layout on disk:
  ~/.skill-manager/sources/<host>__<owner>__<repo>[@ref]/   git clones made from links
  ~/.skill-manager/skills/<name>/                           skills created here
  ~/.skill-manager/trash/                                   anything "deleted" lands here, never hard-deleted
  ~/.skill-manager/notes.json                               user-edited one-line purposes
Each agent folder holds a junction (Windows directory link) to the real skill folder, so one copy serves
Claude Code, Codex and Antigravity and a `git pull` updates all of them at once.
"""
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import platform_support as plat

HOME = Path.home()
STORE = HOME / ".skill-manager"
# Where each agent keeps its config; Claude Code and Codex honour these env vars, so follow them on other people's PCs.
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
AGENT_DIRS = {
    "Claude Code": CLAUDE_HOME / "skills",
    "Codex": CODEX_HOME / "skills",
    "Antigravity": HOME / ".gemini/config/skills",
}
# The login-time updater written earlier for mattpocock/skills re-links every name in its `$names` list.
UPDATER = HOME / ".mattpocock-skills-update.ps1"

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ManagerError(Exception):
    """A failure whose message is fit to show the user as-is."""


# ---- small helpers ------------------------------------------------------------------------------
def run(cmd: list[str], timeout: int = 180) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=plat.NO_WINDOW, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise ManagerError(f"시간 초과: {' '.join(cmd[:4])}…") from None
    if r.returncode:
        raise ManagerError((r.stderr or r.stdout).strip() or f"실패: {' '.join(cmd[:4])}")
    return r.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run(["git", "-C", str(repo), *args])


def is_link(p: Path) -> bool:
    return p.is_symlink() or os.path.isjunction(p)


def target_of(p: Path) -> Path:
    return Path(os.path.realpath(p))


def parse(path: Path) -> dict:
    """Front matter of a SKILL.md -> {name, desc, manual}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, str] = {}
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    key = None
    for line in (m.group(1).splitlines() if m else []):
        if re.match(r"^[A-Za-z_-]+:", line):
            k, v = line.split(":", 1)
            key, meta[k.strip()] = k.strip(), v.strip()
        elif key and line.startswith((" ", "\t")):
            meta[key] += " " + line.strip()
    desc = re.sub(r"^[|>][+-]?\s*", "", meta.get("description", "")).strip().strip("\"'")
    return {"name": meta.get("name", "").strip("\"'") or path.parent.name, "desc": desc,
            "manual": meta.get("disable-model-invocation", "").lower() == "true"}


def discover(root: Path) -> list[Path]:
    """Every skill folder (one that holds a SKILL.md) under root, not descending into a skill."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "node_modules" and not d.startswith(".")]
        if "SKILL.md" in files:
            out.append(Path(dirpath))
            dirs.clear()
    return sorted(out)


# ---- links -> clone location ---------------------------------------------------------------------
_SHORT = re.compile(r"([\w.-]+)/([\w.-]+)(?:/(.+))?")
_URL_PATH = re.compile(r"/(?P<repo>[^/]+/[^/]+?)(?:\.git)?(?:/(?:-/)?(?P<kind>tree|blob)/(?P<ref>[^/]+)/(?P<path>.+?))?/?")
_REF = re.compile(r"[\w][\w./-]*")


def parse_link(text: str) -> tuple[str, str | None, str | None]:
    """-> (clone url, ref, subpath). Accepts owner/repo[/path] (GitHub), or an https repo/tree/blob link.

    shortcut: a ref containing '/' (feature/x) is read as ref 'feature' + path 'x/...'; use the repo root link for those.
    """
    t = text.strip()
    if not t:
        raise ManagerError("링크를 입력하세요")
    if "://" not in t:
        m = _SHORT.fullmatch(t)
        if not m or ":" in t or t.startswith((".", "/", "\\")):
            raise ManagerError("owner/repo 또는 https 링크 형식이 아닙니다")
        return f"https://github.com/{m[1]}/{m[2]}", None, m[3]
    u = urlparse(t)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ManagerError("https 링크만 지원합니다 (계정 정보가 들어간 링크도 불가)")
    m = _URL_PATH.fullmatch(u.path)
    if not m:
        raise ManagerError("저장소 링크를 인식하지 못했습니다 (…/owner/repo 또는 …/tree/브랜치/경로)")
    path = m["path"]
    if m["kind"] == "blob" and path:  # a file link: install the folder that holds it
        path = path.rpartition("/")[0] or None
    if (m["ref"] and not _REF.fullmatch(m["ref"])) or (path and ".." in path.split("/")):
        raise ManagerError("브랜치/경로에 사용할 수 없는 문자가 있습니다")
    return f"https://{u.netloc}/{m['repo']}", m["ref"], path


def fetch(text: str) -> tuple[Path, Path]:
    """Clone (or freshen) the repo behind a link -> (clone dir, folder to look for skills in)."""
    url, ref, sub = parse_link(text)
    p = urlparse(url)
    key = re.sub(r"[^\w.-]+", "__", f"{p.netloc}/{p.path.strip('/')}") + (f"@{ref}" if ref else "")
    dest = STORE / "sources" / key
    if (dest / ".git").exists():
        git(dest, "pull", "--ff-only", "-q")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "clone", "--depth", "1", "-q"]
        try:
            run(cmd + (["--branch", ref] if ref else []) + ["--", url, str(dest)])
        except ManagerError:
            shutil.rmtree(dest, ignore_errors=True)  # only ever a partial clone we just started
            raise
    root = dest / sub if sub else dest
    if not root.resolve().is_relative_to(dest.resolve()) or not root.is_dir():
        raise ManagerError(f"저장소 안에 '{sub}' 경로가 없습니다")
    return dest, root


# ---- origin / version ----------------------------------------------------------------------------
def git_root(p: Path) -> Path | None:
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def origin(skill_dir: Path) -> tuple[str, Path | None]:
    """('git', repo) | ('own', None) | ('link', None) | ('local', None)."""
    if not is_link(skill_dir):
        return "local", None
    t = target_of(skill_dir)
    if STORE.resolve() / "skills" in t.parents:
        return "own", None
    root = git_root(t)
    return ("git", root) if root else ("link", None)


@cache
def version(repo: Path) -> str:
    """'49dd158 · 2026-10-09'. Cached; call version.cache_clear() after anything that moves HEAD."""
    try:
        return git(repo, "log", "-1", "--format=%h · %cs")
    except ManagerError:
        return "?"


def repo_name(repo: Path) -> str:
    try:
        return urlparse(git(repo, "remote", "get-url", "origin")).path.strip("/").removesuffix(".git")
    except ManagerError:
        return repo.name


# ---- install / remove ----------------------------------------------------------------------------
@dataclass
class Result:
    agent: str
    name: str
    status: str  # installed | replaced | exists | conflict


def make_link(link: Path, target: Path) -> None:
    try:
        plat.make_dir_link(link, target)
    except OSError as e:
        raise ManagerError(f"링크를 만들지 못했습니다: {link} ({e})") from e


def trash(p: Path) -> Path:
    dest = STORE / "trash" / f"{time.strftime('%Y%m%d-%H%M%S')}_{p.parent.name}_{p.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), dest)
    return dest


def drop(p: Path) -> None:
    """Remove a skill from an agent folder: a link is just unlinked, a real folder goes to the trash."""
    if is_link(p):
        plat.unlink_dir(p)
    else:
        trash(p)


def install(skill_dirs: list[Path], agents: list[str], replace: bool = False) -> list[Result]:
    out = []
    for src in skill_dirs:
        if not NAME_RE.fullmatch(src.name):
            raise ManagerError(f"폴더 이름을 스킬 이름으로 쓸 수 없습니다: {src.name}")
        for agent in agents:
            link = AGENT_DIRS[agent] / src.name
            status = "installed"
            if is_link(link) and not link.exists():  # dangling link: nothing of the user's to protect
                plat.unlink_dir(link)
            elif link.exists():
                if is_link(link) and target_of(link) == src.resolve():
                    out.append(Result(agent, src.name, "exists"))
                    continue
                if not replace:
                    out.append(Result(agent, src.name, "conflict"))
                    continue
                drop(link)
                status = "replaced"
            link.parent.mkdir(parents=True, exist_ok=True)
            make_link(link, src)
            out.append(Result(agent, src.name, status))
    return out


def forget_in_updater(name: str, path: Path | None = None) -> bool:
    """Stop the login updater from re-linking a skill the user just deleted."""
    p = path or UPDATER
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^(\$names\s*=\s*)(.+)$", text, re.M)
    items = re.findall(r"'([^']+)'", m[2]) if m else []
    if not m or name not in items:
        return False
    items.remove(name)
    p.write_text(text[:m.start(2)] + ",".join(f"'{i}'" for i in items) + text[m.end(2):], encoding="utf-8")
    return True


def remove(agent: str, name: str) -> Path | None:
    """Delete a skill from one agent. -> where a real folder was moved, or None for a plain unlink."""
    if not NAME_RE.fullmatch(name):
        raise ManagerError(f"잘못된 스킬 이름: {name}")
    p = AGENT_DIRS[agent] / name
    if not (p.exists() or is_link(p)):
        raise ManagerError(f"{agent}에 '{name}' 스킬이 없습니다")
    kind, _ = origin(p)
    t = target_of(p)
    if is_link(p):
        plat.unlink_dir(p)
        moved = None
    else:
        moved = trash(p)
    if kind == "own" and not any(target_of(q) == t for d in AGENT_DIRS.values() if d.is_dir()
                                 for q in d.iterdir() if is_link(q)):
        moved = trash(t)  # our own skill, no agent uses it any more
    forget_in_updater(name)
    return moved


def create(name: str, description: str, agents: list[str]) -> tuple[Path, list[Result]]:
    if not NAME_RE.fullmatch(name):
        raise ManagerError("이름은 영문·숫자·-_. 만 쓸 수 있습니다 (예: my-skill)")
    d = STORE / "skills" / name
    if d.exists():
        raise ManagerError(f"이미 만든 스킬입니다: {name}")
    desc = " ".join(description.replace('"', "'").split()) or "이 스킬을 언제 쓰는지 한 줄로 적으세요"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{desc}"\n---\n\n# {name}\n\n이 스킬이 할 일과 따를 단계를 여기에 적으세요.\n',
        encoding="utf-8")
    results = install([d], agents)
    if not any(r.status in ("installed", "replaced") for r in results):
        shutil.rmtree(d)  # nothing links to it, so it is only the empty template we just wrote
        raise ManagerError(f"선택한 에이전트에 같은 이름의 스킬이 이미 있어 만들지 않았습니다: {name}")
    return d, results


# ---- updates -------------------------------------------------------------------------------------
def has_update(repo: Path) -> bool:
    git(repo, "fetch", "-q")
    return git(repo, "rev-parse", "HEAD") != git(repo, "rev-parse", "@{u}")


def relink(repo: Path) -> list[str]:
    """After a pull, re-point links whose skill folder moved inside the repo. -> names that vanished upstream."""
    gone = []
    by_name = {d.name: d for d in discover(repo)}
    root = repo.resolve()
    for d in AGENT_DIRS.values():
        for link in (d.iterdir() if d.is_dir() else []):
            if not (is_link(link) and not link.exists() and root in target_of(link).parents):
                continue
            plat.unlink_dir(link)
            if link.name in by_name:
                make_link(link, by_name[link.name])
            else:
                gone.append(link.name)
    return gone


def update(repo: Path) -> dict:
    old = git(repo, "rev-parse", "--short", "HEAD")
    git(repo, "pull", "--ff-only", "-q")
    version.cache_clear()
    return {"old": old, "new": git(repo, "rev-parse", "--short", "HEAD"), "gone": relink(repo)}


# ---- user notes / editor -------------------------------------------------------------------------
def load_notes() -> dict[str, str]:
    try:
        return json.loads((STORE / "notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_note(name: str, text: str) -> None:
    notes = load_notes()
    if text.strip():
        notes[name] = text.strip()
    else:
        notes.pop(name, None)  # empty = back to the built-in / generated description
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")


def load_settings() -> dict:
    try:
        return json.loads((STORE / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_setting(key: str, value: str) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "settings.json").write_text(json.dumps({**load_settings(), key: value}, indent=1), encoding="utf-8")


def open_in_editor(path: Path) -> None:
    plat.open_path(path)
