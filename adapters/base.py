"""统一会话模型。

所有 adapter 都输出这个结构，`engine.py` 只认它——加一个新数据源
不需要碰统计和渲染。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Message:
    """一条真人消息。adapter 负责把系统注入、工具结果、上下文快照全部挡在外面。

    `text` 可能被截断（防止一次粘贴几万字把词频带偏），但 `full_len` 记原文长度，
    字数统计要用它，否则总量会凭空少掉。
    """
    text: str
    dt: Optional[datetime] = None
    reasoning_chars: int = 0      # 这条消息触发的那次思考有多少字符，没有就是 0
    full_len: int = 0             # 截断前的原文字符数
    truncated: bool = False       # 原文超过上限被截断过

    def __post_init__(self):
        # 统一成带时区的 datetime。adapter 可能给 naive 的，一旦和 aware 的
        # 混在同一份数据里，排序、做差、比较处处都会抛
        # 「can't compare offset-naive and offset-aware datetimes」。
        # 在这里一次性归一，下游就不用各自兜底。
        dt = self.dt
        if dt is not None and dt.tzinfo is None:
            try:
                self.dt = dt.astimezone()
            except (OSError, OverflowError, ValueError):
                # 极早的时间在正时区下 astimezone 会下溢，退回 UTC
                self.dt = dt.replace(tzinfo=timezone.utc)

    @property
    def len(self) -> int:
        return self.full_len or len(self.text)

    @property
    def ts(self) -> float:
        """排序用的时间戳。没有时间戳的排最前。

        不要用 `datetime.min.astimezone()` 当哨兵：naive 的 datetime.min
        按本地时区转 UTC 时，东区会下溢——实测东八区直接抛
        `OSError [Errno 22]`，而且只有「恰好有一条消息缺时间戳」才会
        触发，平时完全看不出来。走 timestamp() 还顺手兼容 naive 与
        aware 混在一起的情况。
        """
        if self.dt is None:
            return 0.0
        try:
            return self.dt.timestamp()
        except (ValueError, OSError, OverflowError):
            return 0.0


@dataclass
class Session:
    id: str
    title: str
    source: str                    # adapter 名：dsh / claude_code / ...
    msgs: list = field(default_factory=list)
    tools: Counter = field(default_factory=Counter)
    approvals_asked: int = 0
    approvals_granted: int = 0
    reasoning_chars: int = 0       # 整个会话里思考文本的总字符数


@dataclass
class LoadReport:
    """读取记账。出了问题让用户看得见，不静默跳过。"""
    source: str = ""
    root: str = ""
    dirs: int = 0                 # 扫到多少个会话目录
    ok: int = 0                   # 成功解析出真人消息的
    skipped_no_user: int = 0      # 有内容但没有真人消息
    read_errors: int = 0          # 读不出来 / 格式不认识
    filtered_injected: int = 0    # 被挡掉的系统注入条数

    def as_dict(self) -> dict:
        return {
            "dirs": self.dirs,
            "ok": self.ok,
            "skipped_no_user": self.skipped_no_user,
            "read_error": self.read_errors,
            "filtered_injected": self.filtered_injected,
        }
