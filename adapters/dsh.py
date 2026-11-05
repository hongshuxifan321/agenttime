"""DeepSeek Harness 会话库 adapter。

目录: ~/.dsh/sessions/<项目>/<session-id>/session[.vN].jsonl.zstd
文件是逐行独立的 zstd 帧，用 stream_reader 解多帧（不能按魔数 28b52ffd 切）。

DSH 把运行时上下文、AGENTS.md、技能目录都写成 `user/message` 事件，
靠 `data.source.kind` 区分：只有 `user` 才是真人说的话，其余必须挡掉。
"""

import glob
import io
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

from .base import LoadReport, Message, Session

ROOT = os.path.join(os.path.expanduser("~"), ".dsh", "sessions")

_LOG_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl\.zstd$")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# 单条消息截断：防止一次粘贴几万字把词频和总字数带偏
MAX_MSG_CHARS = 4000


def detect() -> str | None:
    """返回数据根目录；没有可用数据就返回 None。"""
    if not os.path.isdir(ROOT):
        return None
    if not glob.glob(os.path.join(ROOT, "**", "session*.jsonl.zstd"), recursive=True):
        return None
    return ROOT


def _pick_log(session_dir: str) -> str:
    """一个目录里并存 v2/v3/v4 多份同一日志的格式版本，取版本号最高的。
    它们是同一份日志的不同格式版本，不是增量。"""
    best, best_ver = "", -1
    for fn in os.listdir(session_dir):
        m = _LOG_RE.match(fn)
        if not m:
            continue
        ver = int(m.group(1)) if m.group(1) else 0
        if ver > best_ver:
            best, best_ver = os.path.join(session_dir, fn), ver
    return best


def _read_events(path: str) -> list[dict]:
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise RuntimeError(
            "读 DSH 会话需要 zstandard：pip install zstandard") from exc

    with open(path, "rb") as fh:
        with zstd.ZstdDecompressor().stream_reader(fh) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace").read()

    events = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def _is_real_user(data: dict) -> bool:
    """旧格式没有 source 字段时保守保留（宁可多算，不可漏掉真人消息）。"""
    src = data.get("source")
    if not isinstance(src, dict):
        return True
    return src.get("kind") == "user"


def _local(ts_ms):
    if not isinstance(ts_ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", _SURROGATE_RE.sub("", text)).strip()


def load(root: str = ROOT) -> tuple[list[Session], LoadReport]:
    report = LoadReport(source="dsh", root=root)
    sessions: list[Session] = []

    paths = glob.glob(os.path.join(root, "**", "session*.jsonl.zstd"), recursive=True)
    for session_dir in sorted({os.path.dirname(p) for p in paths}):
        report.dirs += 1
        log = _pick_log(session_dir)
        if not log:
            report.read_errors += 1
            continue
        try:
            events = _read_events(log)
        except Exception:
            report.read_errors += 1
            continue
        if not events:
            report.read_errors += 1
            continue

        title = ""
        msgs: list[Message] = []
        tools = Counter()
        asked = granted = reasoning_total = 0

        for ev in events:
            etype = ev.get("type")
            data = ev.get("data") or {}
            if etype == "session/title":
                title = _tidy(str(data.get("title", "")))
            elif etype == "user/message":
                if not _is_real_user(data):
                    report.filtered_injected += 1
                    continue
                raw = " ".join(
                    c.get("text", "") for c in (data.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "text")
                text = _tidy(raw)
                if not text:
                    continue
                msgs.append(Message(text=text[:MAX_MSG_CHARS],
                                    dt=_local(ev.get("time")),
                                    full_len=len(text),
                                    truncated=len(text) > MAX_MSG_CHARS))
            elif etype == "tool/call":
                name = data.get("name")
                if name:
                    tools[name] += 1
            elif etype == "assistant/message":
                # 一次 assistant 消息里可能有多个 reasoning 块，累加
                reasoning_total += sum(
                    len(c.get("text", ""))
                    for c in (data.get("message") or {}).get("content") or []
                    if isinstance(c, dict) and c.get("type") == "reasoning")
            elif etype == "approval/asked":
                asked += 1
            elif etype == "approval/decided":
                if str(data.get("outcome", "")).lower().startswith("allowed"):
                    granted += 1

        if not msgs:
            report.skipped_no_user += 1
            continue

        msgs.sort(key=lambda m: m.dt or datetime.min.astimezone())
        sessions.append(Session(
            id=os.path.basename(session_dir),
            title=title or "无标题",
            source="dsh",
            msgs=msgs,
            tools=tools,
            approvals_asked=asked,
            approvals_granted=granted,
            reasoning_chars=reasoning_total,
        ))
        report.ok += 1

    return sessions, report
