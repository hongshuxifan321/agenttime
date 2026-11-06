"""DeepSeek Harness 会话库 adapter。

目录: ~/.dsh/sessions/<项目>/<session-id>/session[.vN].jsonl.zstd
文件是逐行独立的 zstd 帧，用 stream_reader 解多帧（不能按魔数 28b52ffd 切）。

DSH 把运行时上下文、AGENTS.md、技能目录都写成 `user/message` 事件，
靠 `data.source.kind` 区分：只有 `user` 才是真人说的话，其余必须挡掉。
"""

from __future__ import annotations

import glob
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

# 逐帧解压时每次喂给 decompress 的字节数
_CHUNK = 1 << 16


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


def _decode_frames(data: bytes) -> tuple[str, bool]:
    """逐帧解压，顺便回答「文件是不是被截断了」。

    `stream_reader` 读不完一帧只会安静停下，看起来和「这条会话只有这么多
    内容」一模一样——实测把一个两条消息的文件切一半，它能正常返回 1 条，
    调用方无从判断。用 `decompressobj` 逐帧解、看每帧的 `eof` 才分得清
    「读完了」和「读到这里就没了」。

    必须按块喂：把剩余的全部字节反复交给 decompress 是 O(n^2)，126MB 的
    会话库会被拖到分钟级。unused_data 总是本次输入的后缀，据此推进位置。
    """
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise RuntimeError(
            "读 DSH 会话需要 zstandard：pip install zstandard") from exc

    out = []
    n = len(data)
    pos = 0
    complete = True
    while pos < n:
        dec = zstd.ZstdDecompressor().decompressobj()
        while pos < n:
            end = min(pos + _CHUNK, n)
            out.append(dec.decompress(data[pos:end]))
            if dec.eof:
                pos = end - len(dec.unused_data)
                break
            pos = end
        if not dec.eof:
            complete = False
            break
    return b"".join(out).decode("utf-8", "replace"), complete


def _read_events(path: str, report: LoadReport | None = None) -> tuple[list, bool]:
    """返回 (事件列表, 文件是否完整)。不完整时把已解出的部分交回去用。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return [], False
    text, complete = _decode_frames(data)

    events = []
    for line_no, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if report is not None:
                report.malformed_events += 1
                hint = f"{os.path.basename(path)}: 第 {line_no} 行不是合法 JSON"
                if len(report.errors) < 3 and hint not in report.errors:
                    report.errors.append(hint)
    return events, complete


def _is_real_user(data: dict) -> bool:
    """旧格式没有 source 字段时保守保留（宁可多算，不可漏掉真人消息）。"""
    if "source" not in data:
        return True
    src = data["source"]
    return isinstance(src, dict) and src.get("kind") == "user"


def _local(ts):
    """DSH 现在写毫秒时间戳。

    数字字符串和 ISO 也一并认——它哪天改了时间字段的格式，如果这里只认数字，
    结果会是每条消息都没有时间戳、`build_facts` 直接报「没有带时间戳的
    用户消息」，而用户完全看不出是格式变了。
    """
    if isinstance(ts, bool):
        return None
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
        try:
            ts = float(s)
        except ValueError:
            return None
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", _SURROGATE_RE.sub("", text)).strip()


def load(root: str = ROOT) -> tuple[list[Session], LoadReport]:
    # 先确认依赖在不在。不这么写的话，缺 zstandard 会变成「每个文件都读不出来」，
    # 用户看到的是一句「96 个读取失败」，完全看不出是 pip install 没做。
    try:
        import zstandard  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "读 DSH 会话需要 zstandard：pip install zstandard") from exc

    report = LoadReport(source="dsh", root=root)
    sessions: list[Session] = []

    paths = glob.glob(os.path.join(root, "**", "session*.jsonl.zstd"), recursive=True)
    for session_dir in sorted({os.path.dirname(p) for p in paths}):
        report.dirs += 1
        try:
            log = _pick_log(session_dir)
        except OSError as exc:
            # 一个目录权限异常不该拖垮整次统计
            report.read_errors += 1
            msg = f"{os.path.basename(session_dir)}: {exc}"
            if msg not in report.errors and len(report.errors) < 3:
                report.errors.append(msg)
            continue
        if not log:
            report.read_errors += 1
            continue
        try:
            events, complete = _read_events(log, report)
        except Exception as exc:
            report.read_errors += 1
            msg = f"{os.path.basename(log)}: {str(exc) or exc.__class__.__name__}"
            if msg not in report.errors and len(report.errors) < 3:
                report.errors.append(msg)
            continue
        if not events:
            report.read_errors += 1
            continue
        if not complete:
            # 截断的文件仍然把已读到的部分算进去，但必须说出来：不报的话，
            # 报告会安静地少掉一部分消息，读者完全看不出来。
            report.read_errors += 1
            msg = f"{os.path.basename(log)}: 文件不完整（可能正在写入或中断过）"
            if msg not in report.errors and len(report.errors) < 3:
                report.errors.append(msg)

        title = ""
        msgs: list[Message] = []
        tools = Counter()
        asked = granted = denied = cancelled = decided = unknown = reasoning_total = 0

        for ev in events:
            # 事件形状不可信。一行是 JSON 数组、`data` 是字符串、`message` 是
            # 字符串，`.get` 都会当场抛——而那个异常会冲出 load()，最后表现成
            # 「整个数据源读取失败」，一条坏行废掉全部会话。
            if not isinstance(ev, dict):
                report.malformed_events += 1
                continue
            etype = ev.get("type")
            data = ev.get("data")
            if data is None:
                data = {}
            elif not isinstance(data, dict):
                report.malformed_events += 1
                continue

            if etype == "session/title":
                # 标题是 null 时 str(None) 会变成字面量「None」进到报告里
                title = _tidy(str(data.get("title") or ""))
            elif etype == "user/message":
                if "source" in data and (not isinstance(data["source"], dict)
                        or not isinstance(data["source"].get("kind"), str)
                        or not data["source"]["kind"]):
                    report.malformed_events += 1
                    if len(report.errors) < 3:
                        hint = f"{os.path.basename(log)}: user/message 的 source 格式不对"
                        if hint not in report.errors:
                            report.errors.append(hint)
                    continue
                if not _is_real_user(data):
                    report.filtered_injected += 1
                    continue
                content = data.get("content")
                if content is None:
                    content = []
                elif not isinstance(content, list):
                    report.malformed_events += 1
                    continue
                raw = " ".join(
                    c["text"] for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                    and isinstance(c.get("text"), str))
                text = _tidy(raw)
                if not text:
                    continue
                msgs.append(Message(text=text[:MAX_MSG_CHARS],
                                    dt=_local(ev.get("time")),
                                    full_len=len(text),
                                    truncated=len(text) > MAX_MSG_CHARS))
            elif etype == "tool/call":
                name = data.get("name")
                if isinstance(name, str) and name:
                    tools[name] += 1
            elif etype == "assistant/message":
                # 一次 assistant 消息里可能有多个 reasoning 块，累加
                message = data.get("message")
                blocks = message.get("content") if isinstance(message, dict) else None
                if not isinstance(blocks, list):
                    blocks = []
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "reasoning":
                        continue
                    piece = block.get("text")
                    if isinstance(piece, str):
                        reasoning_total += len(piece)
            elif etype == "approval/asked":
                asked += 1
            elif etype == "approval/decided":
                decided += 1
                outcome = str(data.get("outcome") or "").lower()
                if outcome.startswith("allowed"):
                    granted += 1
                elif outcome == "rejected":
                    denied += 1
                elif outcome == "cancelled":
                    cancelled += 1
                else:
                    unknown += 1

        if not msgs:
            report.skipped_no_user += 1
            continue

        msgs.sort(key=lambda m: m.ts)
        sessions.append(Session(
            id=os.path.basename(session_dir),
            title=title or "无标题",
            source="dsh",
            msgs=msgs,
            tools=tools,
            approvals_asked=asked,
            approvals_granted=granted,
            approvals_denied=denied,
            approvals_cancelled=cancelled,
            approvals_pending=max(0, asked - decided),
            approvals_unknown=unknown,
            reasoning_chars=reasoning_total,
        ))
        report.ok += 1

    return sessions, report
