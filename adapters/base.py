"""统一会话模型。

所有 adapter 都输出这个结构，`engine.py` 只认它——加一个新数据源
不需要碰统计和渲染。
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
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

    @property
    def len(self) -> int:
        return self.full_len or len(self.text)


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
