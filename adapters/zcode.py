"""ZCode 会话库 adapter。

数据在 sqlite：~/.zcode/cli/db/db.sqlite（只读打开，不会锁住正在用的库）
- session 表：id / title / time_created / time_deleted / directory
- message 表：id / session_id / sequence / data(JSON: role, time.created)
- part 表：message_id / sequence / data(JSON: type, text, synthetic)
   part.type 有 text / reasoning / tool / file / step-start / step-finish
- tool_usage 表：tool_name / approval_status

和 Claude Code 一样，注入要靠 `synthetic` 标记和自己判断挡：
`part.data.synthetic == true` 的是系统补的内容，不算用户输入。
"""

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from .base import LoadReport, Message, Session

DB = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")

MAX_MSG_CHARS = 4000


def detect() -> str | None:
    return DB if os.path.isfile(DB) else None


def _local(ms):
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def load(db_path: str = DB) -> tuple[list[Session], LoadReport]:
    report = LoadReport(source="zcode", root=db_path)
    sessions: list[Session] = []
    if not os.path.isfile(db_path):
        return sessions, report

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        report.read_errors += 1
        return sessions, report

    try:
        titles, created, deleted = {}, {}, {}
        for sid, title, tc, td in con.execute(
                "SELECT id, title, time_created, time_deleted FROM session"):
            titles[sid] = title or ""
            created[sid] = tc
            deleted[sid] = td
            report.dirs += 1

        # 消息：role 和时间都在 data JSON 里
        msg_meta = {}
        for mid, sid, raw in con.execute("SELECT id, session_id, data FROM message"):
            try:
                d = json.loads(raw)
            except Exception:
                continue
            msg_meta[mid] = (sid, d.get("role"), _local((d.get("time") or {}).get("created")))

        # 分片：text 是正文，reasoning 是思考，tool 是工具调用
        msgs_by_sid = {}
        think_by_sid = Counter()
        tools_by_sid = {}
        for mid, raw in con.execute("SELECT message_id, data FROM part"):
            meta = msg_meta.get(mid)
            if not meta:
                continue
            sid, role, dt = meta
            try:
                d = json.loads(raw)
            except Exception:
                continue
            ptype = d.get("type")
            if ptype == "reasoning":
                think_by_sid[sid] += len(d.get("text") or "")
            elif ptype == "tool":
                name = d.get("tool")
                if name:
                    tools_by_sid.setdefault(sid, Counter())[name] += 1
            elif ptype == "text":
                if d.get("synthetic"):
                    report.filtered_injected += 1
                    continue
                if role != "user":
                    continue
                text = (d.get("text") or "").strip()
                if not text:
                    continue
                msgs_by_sid.setdefault(sid, []).append(
                    Message(text=text[:MAX_MSG_CHARS], dt=dt,
                            full_len=len(text), truncated=len(text) > MAX_MSG_CHARS))

        for sid, msgs in msgs_by_sid.items():
            if deleted.get(sid):
                report.skipped_no_user += 1      # 已删除的会话不算
                continue
            if not msgs:
                continue
            msgs.sort(key=lambda m: m.dt or datetime.min.astimezone())
            sessions.append(Session(
                id=sid,
                title=titles.get(sid) or "无标题",
                source="zcode",
                msgs=msgs,
                tools=tools_by_sid.get(sid, Counter()),
                reasoning_chars=think_by_sid.get(sid, 0),
            ))
            report.ok += 1
    finally:
        con.close()

    return sessions, report
