"""统计层：吃统一 Session 列表，产出 facts。

不含任何 IO 和格式解析——加一个新 agent 只需要写一个 adapter，
这个文件不用改。
"""

import re
from collections import Counter
from datetime import datetime

from adapters.base import LoadReport, Session

# ── 行为关键词：用成词/词边界，不用单字，否则「对」「不是」会系统性误报 ──
RE_THANK = re.compile(r"谢谢|感谢|thanks|thank\s*you", re.I)
RE_INTERRUPT = re.compile(r"算了|停下|别跑了|别弄了|等等|先别|不要了|\bstop\b", re.I)
RE_CONFIRM = re.compile(
    r"^(好的|好|ok|okay|可以|行|嗯|没错|没问题|对的|对|是的|收到)\W*$", re.I)

# 英文词要删掉路径/URL/代码残片：先把整段路径与 URL 抹掉再切词，
# 否则 C:\Users\me\Desktop 会被切成 users / desktop 这种高频假词
_STRIP_URLS = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s，。；、）】\"']*)|(?:https?://\S+)|(?:[\w.-]+@[\w.-]+)")
_PATHISH = re.compile(r"[\\/:@#$%^&*<>|{}\[\]`~=+]")

_STOP_EN = set("""the a an is are was were be been being to of in for on with at by from as it its
that this these those i me my we our you your he she they them and or if but not no so do does did
will would could should may might can shall have has had what which who whom when where why how all
both each few more most other some such only own same than too very just because while here there
then once again into through during before after above below between under about""".split())

# 中文虚字：二字词里只要含这些就滤掉
_CN_STOP = set("的了在是有和就都与及或一个不以也不被把从为着这那它他她们我你其什么没还在可并但只又很能后前中上下回见说做想要让给用对向")

# 实测里稳定复现的跨词碎片与口语虚词，手工剔除
_CN_NOISE = set("件夹 面板 可以 帮我 这个 那个 什么 怎么 就是 然后 现在 时候 东西 地方 看看 一下 "
                "哪些 如果 这些 因为 所以 还是 但是 已经 觉得 知道 需要 应该 可能 其实 不要 不能 没有".split())


def _sittings(msgs: list, gap_minutes: int = 90) -> list[list]:
    """把消息按「间隔超过 90 分钟」切成一次坐下。

    会话可以隔几天恢复，直接用首末时间算时长会得到 453 小时这种数。
    """
    sits, cur, prev = [], [], None
    for m in msgs:
        if m.dt is None:
            continue
        if prev is not None and (m.dt - prev).total_seconds() > gap_minutes * 60:
            sits.append(cur)
            cur = []
        cur.append(m)
        prev = m.dt
    if cur:
        sits.append(cur)
    return sits


def _word_stats(msgs: list) -> tuple[list, list]:
    """英文词 + 中文二字词，剔除路径、URL、代码残片。"""
    en, cn = Counter(), Counter()
    for m in msgs:
        clean = _STRIP_URLS.sub(" ", m.text)
        for w in re.findall(r"[A-Za-z][A-Za-z0-9+_-]{1,20}", clean):
            wl = w.lower()
            if len(wl) < 3 or wl in _STOP_EN or _PATHISH.search(w) or wl.isdigit():
                continue
            en[wl] += 1
        for part in re.findall(r"[\u4e00-\u9fff]+", m.text):
            for i in range(len(part) - 1):
                bg = part[i:i + 2]
                if bg[0] in _CN_STOP or bg[1] in _CN_STOP or bg in _CN_NOISE:
                    continue
                cn[bg] += 1
    return en.most_common(20), cn.most_common(20)


def build_facts(sessions: list[Session], report: LoadReport | None = None) -> dict:
    report = report or LoadReport()
    all_msgs = [m for s in sessions for m in s.msgs]
    all_msgs.sort(key=lambda m: m.dt or datetime.min.astimezone())
    dated = [m for m in all_msgs if m.dt]
    if not dated:
        raise ValueError("没有带时间戳的用户消息")

    total_chars = sum(m.len for m in all_msgs)
    dates = sorted({m.dt.strftime("%Y-%m-%d") for m in dated})
    first, last = dated[0], dated[-1]
    total_days = (last.dt.date() - first.dt.date()).days + 1

    # 最长连续
    streak = best = 1
    for a, b in zip(dates, dates[1:]):
        d1 = datetime.strptime(a, "%Y-%m-%d")
        d2 = datetime.strptime(b, "%Y-%m-%d")
        streak = streak + 1 if (d2 - d1).days == 1 else 1
        best = max(best, streak)

    per_day = Counter(m.dt.strftime("%Y-%m-%d") for m in dated)
    busiest_day, busiest_n = per_day.most_common(1)[0]
    busiest_titles = Counter(
        s.title for s in sessions for m in s.msgs
        if m.dt and m.dt.strftime("%Y-%m-%d") == busiest_day)

    hour_dist = Counter(m.dt.hour for m in dated)
    late = [m for m in dated if m.dt.hour < 5]
    late_sessions = len({id(s) for s in sessions for m in s.msgs
                         if m.dt and m.dt.hour < 5})

    sits = _sittings(dated)
    en_words, cn_words = _word_stats(all_msgs)
    shortest = min(all_msgs, key=lambda m: m.len)
    longest = max(all_msgs, key=lambda m: m.len)

    tools = Counter()
    reasoning = approvals_asked = approvals_granted = 0
    for s in sessions:
        tools.update(s.tools)
        reasoning += s.reasoning_chars
        approvals_asked += s.approvals_asked
        approvals_granted += s.approvals_granted

    interrupt_n = sum(1 for m in all_msgs if RE_INTERRUPT.search(m.text))
    thank_n = sum(1 for m in all_msgs if RE_THANK.search(m.text))
    confirm_n = sum(1 for m in all_msgs if RE_CONFIRM.match(m.text.strip()))

    # 项目线：会话按消息数排，带上日期区间
    projects = []
    for s in sessions:
        ds = [m.dt for m in s.msgs if m.dt]
        if not ds:
            continue
        projects.append({
            "title": s.title,
            "n": len(s.msgs),
            "start": min(ds).strftime("%m-%d"),
            "end": max(ds).strftime("%m-%d"),
            "tools": sum(s.tools.values()),
        })
    projects.sort(key=lambda p: -p["n"])

    return {
        "span": {"first": first.dt.strftime("%Y-%m-%d %H:%M"),
                 "first_time": first.dt.strftime("%H:%M"),
                 "first_hour": first.dt.hour,
                 "last": last.dt.strftime("%Y-%m-%d %H:%M"),
                 "first_date": first.dt.strftime("%Y年%m月%d日"),
                 "first_text": first.text[:120],
                 "last_text": last.text[:120],
                 "total_days": total_days},
        "volume": {"sessions": len(sessions), "msgs": len(all_msgs),
                   "chars": total_chars,
                   "avg_chars": round(total_chars / len(all_msgs), 1)},
        "rhythm": {"active_days": len(dates), "max_streak": best,
                   "sittings": len(sits),
                   "avg_per_sitting": round(len(all_msgs) / max(1, len(sits)), 1),
                   "busiest_day": busiest_day, "busiest_n": busiest_n,
                   "busiest_titles": busiest_titles.most_common(4),
                   "hour_dist": {h: hour_dist.get(h, 0) for h in range(24)},
                   "peak_hour": hour_dist.most_common(1)[0][0],
                   "peak_count": hour_dist.most_common(1)[0][1],
                   "late_msgs": len(late), "late_sessions": late_sessions,
                   "latest": max(late, key=lambda m: m.dt).dt.strftime("%m 月 %d 日 %H:%M")
                   if late else None},
        "words": {"en": en_words, "cn": cn_words,
                  "shortest": {"len": shortest.len, "text": shortest.text[:60]},
                  "longest": {"len": longest.len, "text": longest.text[:60],
                              "truncated": longest.truncated},
                  "thanks": thank_n, "confirms": confirm_n, "interrupts": interrupt_n},
        "agent": {"tools_total": sum(tools.values()),
                  "tools_top": tools.most_common(6),
                  "tools_by_name": dict(tools),
                  "reasoning_chars": reasoning,
                  "ratio": round(reasoning / max(1, total_chars), 1),
                  "read_days": round(reasoning / 10 / 86400),
                  "approvals_asked": approvals_asked,
                  "approvals_granted": approvals_granted,
                  "denied": approvals_asked - approvals_granted},
        "projects": projects[:10],
        "deepest_project": max(projects, key=lambda p: p["tools"]) if projects else None,
        # 数据来自哪几个 agent、各贡献多少会话——合并多源时用来核对口径
        "sources": dict(Counter(s.source for s in sessions)),
        "quality": report.as_dict(),
    }
