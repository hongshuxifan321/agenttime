"""Claude Code 会话库 adapter。

目录: ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
子代理会话单独存成 agent-*.jsonl，不算用户自己的会话，跳过。

格式参考 https://github.com/neilberkman/ccrider/blob/v1.1.8/research/schema.md
每行一个 JSON，类型有 summary / user / assistant / system / file-history-snapshot。

和 DSH 不同，Claude Code 没有 `source.kind` 这种干净字段，注入要靠三件事挡：
1. `isMeta: true` 的条目
2. user 消息里 `type == "tool_result"` 的块
3. 文本里的 XML 标签（<system-reminder>、<command-name> 等）
"""

import glob
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

from .base import LoadReport, Message, Session

ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# 整段 XML 注入块，跨行匹配
_INJECT_BLOCKS = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|local-command-stderr|ide_opened_file|ide_selection)>"
    r".*?</\1>",
    re.S | re.I,
)
_TAG_ONLY = re.compile(r"<[^>]{1,80}>")
_WS = re.compile(r"\s+")

MAX_MSG_CHARS = 4000


def detect() -> str | None:
    if not os.path.isdir(ROOT):
        return None
    if not glob.glob(os.path.join(ROOT, "*", "*.jsonl")):
        return None
    return ROOT


def _parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _clean(text: str) -> str:
    """去掉 XML 注入块，再去掉残留的孤立标签。"""
    text = _INJECT_BLOCKS.sub(" ", text)
    text = _TAG_ONLY.sub(" ", text)
    return _WS.sub(" ", _SURROGATE_RE.sub("", text)).strip()


def _user_text(entry: dict) -> str:
    """从 user 条目里取真人输入。数组形式里只有 text 块算数，tool_result 不算。"""
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return _clean(content)
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        return _clean(" ".join(parts))
    return ""


def _assistant_bits(entry: dict) -> tuple[int, list[str]]:
    """返回 (思考字符数, 工具名列表)。thinking 块的字段是 .thinking，不是 .text。"""
    think_chars = 0
    tools = []
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return 0, []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "thinking":
            think_chars += len(c.get("thinking") or "")
        elif c.get("type") == "tool_use":
            name = c.get("name")
            if name:
                tools.append(name)
    return think_chars, tools


def load(root: str = ROOT) -> tuple[list[Session], LoadReport]:
    report = LoadReport(source="claude_code", root=root)
    sessions: list[Session] = []

    files = sorted(glob.glob(os.path.join(root, "*", "*.jsonl")))
    for path in files:
        name = os.path.basename(path)
        if name.startswith("agent-"):
            continue                      # 子代理会话，不是用户自己的
        report.dirs += 1

        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().split("\n")
        except OSError:
            report.read_errors += 1
            continue

        title = ""
        msgs: list[Message] = []
        tools = Counter()
        reasoning_total = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = entry.get("type")
            if etype == "summary":
                title = _clean(str(entry.get("summary", "")))
            elif etype == "user":
                if entry.get("isMeta"):
                    report.filtered_injected += 1
                    continue
                raw = _user_text(entry)
                if not raw:
                    report.filtered_injected += 1
                    continue
                msgs.append(Message(text=raw[:MAX_MSG_CHARS],
                                    dt=_parse_ts(entry.get("timestamp")),
                                    full_len=len(raw),
                                    truncated=len(raw) > MAX_MSG_CHARS))
            elif etype == "assistant":
                chars, names = _assistant_bits(entry)
                reasoning_total += chars
                tools.update(names)

        if not msgs:
            report.skipped_no_user += 1
            continue

        msgs.sort(key=lambda m: m.dt or datetime.min.astimezone())
        sessions.append(Session(
            id=os.path.splitext(name)[0],
            title=title or "无标题",
            source="claude_code",
            msgs=msgs,
            tools=tools,
            reasoning_chars=reasoning_total,
        ))
        report.ok += 1

    return sessions, report
