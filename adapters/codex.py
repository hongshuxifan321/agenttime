"""OpenAI Codex 会话库 adapter。

目录: ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl.zst（旧会话在 archived_sessions）

真实对话在 `event_msg` 的 `item_completed` 里：
    item.type == "UserMessage"   → content[].text   真人说的话
    item.type == "AgentMessage"  → content[].text   模型的回复
    item.type == "Reasoning"     → summary_text / raw_content，实测都是空，拿不到思考量

**不要读 `response_item`**：那里的 role=user 是发给模型的完整请求，混着系统提示
（实测两种：30K 字符的 "The following is the Codex..." 和 7K 字符的 "# AGENTS.md ..."），
当成用户消息会让字数虚高 30 倍、标题全变成 "# AGENTS.md in"。

拿不到的：思考量、工具调用、审批事件。这不是适配没做，是数据里就没有。
"""

import glob
import io
import json
import os
from collections import Counter
from datetime import datetime, timezone

from .base import LoadReport, Message, Session

ROOT = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
ARCHIVE = os.path.join(os.path.expanduser("~"), ".codex", "archived_sessions")

MAX_MSG_CHARS = 4000

# 实测这台机器上 74 个会话的 UserMessage 全是系统提示，一条真人输入都没有。
# 下面几条把它们挡掉；挡完如果没有剩的，doctor 会显示 0 个会话——
# 这比拿系统提示凑一份报告诚实。
_SYSTEM_PREFIXES = (
    "The following is the Codex",
    "# AGENTS.md",
    "<turn_aborted>",
    "<environment_context>",
)


def _is_injected(text: str) -> bool:
    if text.startswith(_SYSTEM_PREFIXES):
        return True
    # 长文本里出现指令文件字样，基本可以确定是注入
    return len(text) > 5000 and ("AGENTS.md" in text or "system prompt" in text.lower())


def detect() -> str | None:
    for root in (ROOT, ARCHIVE):
        if os.path.isdir(root) and glob.glob(
                os.path.join(root, "**", "*.jsonl.zst"), recursive=True):
            return root
    return None


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


def _read_lines(path: str):
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise RuntimeError(
            "读 Codex 会话需要 zstandard：pip install zstandard") from exc

    with open(path, "rb") as fh:
        with zstd.ZstdDecompressor().stream_reader(fh) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace").read()
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _item_text(item: dict) -> str:
    """UserMessage 的 content 是 [{type: "text", text: ...}]，AgentMessage 里 type 是大写 Text。"""
    parts = []
    for c in item.get("content") or []:
        if isinstance(c, dict) and str(c.get("type", "")).lower() == "text":
            t = c.get("text")
            if isinstance(t, str):
                parts.append(t)
    return " ".join(parts).strip()


def load(root: str = ROOT) -> tuple[list[Session], LoadReport]:
    report = LoadReport(source="codex", root=root)
    sessions: list[Session] = []

    for path in sorted(glob.glob(os.path.join(root, "**", "*.jsonl.zst"), recursive=True)):
        report.dirs += 1
        try:
            events = list(_read_lines(path))
        except Exception:
            report.read_errors += 1
            continue

        msgs: list[Message] = []
        title = ""
        for ev in events:
            if ev.get("type") != "event_msg":
                continue
            payload = ev.get("payload") or {}
            if payload.get("type") != "item_completed":
                continue
            item = payload.get("item") or {}
            if item.get("type") != "UserMessage":
                continue
            text = _item_text(item)
            if not text or _is_injected(text):
                report.filtered_injected += 1
                continue
            if not title:
                title = text[:24].replace("\n", " ")
            dt = _parse_ts(ev.get("timestamp"))
            if dt is None and isinstance(payload.get("completed_at_ms"), (int, float)):
                dt = datetime.fromtimestamp(payload["completed_at_ms"] / 1000,
                                            tz=timezone.utc).astimezone()
            msgs.append(Message(text=text[:MAX_MSG_CHARS], dt=dt,
                                full_len=len(text), truncated=len(text) > MAX_MSG_CHARS))

        if not msgs:
            report.skipped_no_user += 1
            continue

        msgs.sort(key=lambda m: m.dt or datetime.min.astimezone())
        sessions.append(Session(
            id=os.path.basename(path).replace(".jsonl.zst", ""),
            title=title or "无标题",
            source="codex",
            msgs=msgs,
            tools=Counter(),
            reasoning_chars=0,
        ))
        report.ok += 1

    return sessions, report
