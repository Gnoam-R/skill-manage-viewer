"""How often Claude Code used each skill, per month, counted from its conversation logs.

Claude Code keeps every conversation as JSON lines under ~/.claude/projects/**/*.jsonl. A skill shows up there in
two ways: the agent calling it (a `Skill` tool call) and the user typing `/name` (a `<command-name>` tag). The logs
are large, so each file is read once and its events are cached by size and modification time; afterwards only new or
grown files are read again.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

COMMAND = re.compile(r"<command-name>/([^<\s]+)</command-name>")
ALL = "전체"  # key for the sum over every month


def month_of(timestamp: object) -> str | None:
    """'2026-10-19T04:18:40Z' -> '2026-10' in this PC's time zone (a late-night call belongs to the local month)."""
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m")
    except ValueError:
        return None


def scan_file(path: Path) -> list[list[str]]:
    """[event id, month, skill] for every skill call in one log file. Unreadable lines are skipped."""
    events = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"Skill"' not in line and "<command-name>" not in line:
                continue  # cheap filter (spacing-agnostic: another tool may write `"name": "Skill"`); most lines are something else
            try:
                d = json.loads(line)
            except ValueError:
                continue
            month = month_of(d.get("timestamp"))
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = msg.get("content")
            if not month:
                continue
            blocks = content if isinstance(content, list) else []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                    skill = (b.get("input") or {}).get("skill")
                    if isinstance(skill, str) and skill:
                        events.append([str(b.get("id") or d.get("uuid")), month, skill])
            if d.get("type") == "user":
                text = (
                    content
                    if isinstance(content, str)
                    else " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
                )
                for name in COMMAND.findall(text):
                    events.append([f"{d.get('uuid')}:{name}", month, name])
    return events


def monthly_counts(projects: Path, cache_file: Path) -> dict[str, Counter]:
    """{'2026-10': Counter({'tdd': 3, ...}), ..., ALL: Counter(...)}. Built-in commands like /clear are counted too;
    the caller keeps only names that are skills."""
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    fresh = {}
    for f in sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []:
        try:
            st = f.stat()
            entry = cache.get(str(f))
            if not (isinstance(entry, dict) and entry.get("size") == st.st_size and entry.get("mtime") == st.st_mtime):
                entry = {"size": st.st_size, "mtime": st.st_mtime, "events": scan_file(f)}
        except OSError:
            continue  # a log being written or removed right now: count it next time
        fresh[str(f)] = entry
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(fresh), encoding="utf-8")
    except OSError:
        pass  # counting still works, it is only slower next time
    counts: dict[str, Counter] = defaultdict(Counter)
    seen: set[str] = set()
    for entry in fresh.values():
        for event_id, month, skill in entry["events"]:
            if event_id in seen:  # a resumed conversation can repeat earlier lines in a new file
                continue
            seen.add(event_id)
            counts[month][skill] += 1
            counts[ALL][skill] += 1
    return dict(counts)


def count_for(counter: Counter, name: str) -> int:
    """Calls of an installed skill. The log may name it with a plugin prefix the list doesn't show
    ('anthropic-skills:pdf' for 'pdf') or without one ('brainstorming' for 'superpowers:brainstorming')."""
    short = name.split(":")[-1]
    return sum(n for logged, n in counter.items() if logged == name or logged.split(":")[-1] == short)
