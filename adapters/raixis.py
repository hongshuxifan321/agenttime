"""锐识 AI（raixis）会话库 adapter。

目录: ~/.raixis/sessions/*.json
每个 json 是一整个会话：title / workspace / createdAt / piMessages。
piMessages 里是真正的一问一答：[{role, content, timestamp}]。

同一目录下的 *.jsonl 是底层事件流，这里不读——json 里的 piMessages 已经够了。
"""

import glob
import json
import os
from collections import Counter
from datetime import datetime, timezone

from .base import LoadReport, Message, Session

ROOT = os.path.join(os.path.expanduser("~"), ".raixis", "sessions")

MAX_MSG_CHARS = 4000


def detect() -> str | None:
    if not os.path.isdir(ROOT):
        return None
    if not glob.glob(os.path.join(ROOT, "*.json")):
        return None
    return ROOT


def _parse_ts(value):
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    return None


def _text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                t = c.get("text") or c.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts).strip()
    return ""


def load(root: str = ROOT) -> tuple[list[Session], LoadReport]:
    report = LoadReport(source="raixis", root=root)
    sessions: list[Session] = []

    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        report.dirs += 1
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                obj = json.load(fh)
        except Exception:
            report.read_errors += 1
            continue
        if not isinstance(obj, dict):
            report.read_errors += 1
            continue

        msgs: list[Message] = []
        for m in obj.get("piMessages") or []:
            if not isinstance(m, dict):
                continue
            if m.get("role") != "user":
                continue
            text = _text(m.get("content"))
            if not text:
                report.filtered_injected += 1
                continue
            msgs.append(Message(
                text=text[:MAX_MSG_CHARS],
                dt=_parse_ts(m.get("timestamp")) or _parse_ts(obj.get("createdAt")),
                full_len=len(text),
                truncated=len(text) > MAX_MSG_CHARS))

        if not msgs:
            report.skipped_no_user += 1
            continue

        msgs.sort(key=lambda x: x.dt or datetime.min.astimezone())
        sessions.append(Session(
            id=os.path.splitext(os.path.basename(path))[0],
            title=(obj.get("title") or "无标题").strip(),
            source="raixis",
            msgs=msgs,
            tools=Counter(),
            reasoning_chars=0,
        ))
        report.ok += 1

    return sessions, report
