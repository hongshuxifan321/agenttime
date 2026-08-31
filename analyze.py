"""
Agenttime 深度分析引擎
支持: Claude Code (JSONL) + ChatGPT (conversations.json)
统一内部数据模型 → 分析 → 报告
"""

import json, glob, os, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


STOPWORDS_EN = {
    "the","a","an","is","are","was","were","be","been","being",
    "have","has","had","do","does","did","will","would","could",
    "should","may","might","can","shall","to","of","in","for",
    "on","with","at","by","from","as","into","through","during",
    "before","after","above","below","between","under","again",
    "then","once","here","there","when","where","why","how",
    "all","both","each","few","more","most","other","some","such",
    "no","nor","not","only","own","same","so","than","too",
    "very","just","because","but","and","or","if","while",
    "it","its","that","this","these","those",
    "i","me","my","we","our","you","your","he","she","they",
    "them","what","which","who","whom",
}
CODE_PAT = re.compile(r'^[{}[\],:;"\'()=<>/\\|@#$%^&*+~`._-]+$')

def is_meaningful(w: str) -> bool:
    w = w.strip().lower()
    if len(w) <= 1 or w in STOPWORDS_EN or w.isdigit(): return False
    # JSON 键残留（"type": 等）— 含引号或冒号直接滤掉
    if '"' in w or ':' in w: return False
    # 日志/JSON 残片（[request、user] 等）— 首尾括号直接滤掉
    if w.startswith(("[", "{")) or w.endswith(("]", "}")): return False
    return not CODE_PAT.match(w)

# 中文虚词/高频功能字——过滤「的了」「是的」这类 bigram 噪声
CN_FUNCTION_CHARS = set("的了在是有和就都与及或一个不以也不被把从为着这那它他她们我你其什么没还在可并但只又很能后前中上下回见说做想要让给用对向和")

def is_meaningful_bigram(b: str) -> bool:
    """二字词只要含虚字就过滤，保留有内容含义的词。"""
    return not (b[0] in CN_FUNCTION_CHARS or b[1] in CN_FUNCTION_CHARS)

MAX_MSG_CHARS = 2000

# 数据里的孤立代理字符(JSON \uXXXX 转义可能产生非法码元), 统计前剔除
_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')

def extract_text(cl: list) -> str:
    """提取文本并截断单条消息, 防止巨型粘贴污染总量与高频词统计。"""
    text = " ".join(c.get("text","") for c in cl if isinstance(c,dict) and c.get("type")=="text")
    return _SURROGATE_RE.sub("", text)[:MAX_MSG_CHARS]


# ═══════════════════════════════════════════════════
# 数据源解析器
# ═══════════════════════════════════════════════════

def parse_claude_code(project_dir: str) -> list[dict]:
    """解析 Claude Code JSONL → 统一会话列表"""
    jsonl_files = list(set(
        glob.glob(os.path.join(project_dir, "*", "*.jsonl")) +
        glob.glob(os.path.join(project_dir, "*.jsonl"))
    ))
    # 排除 subagents/ 子目录: 子代理会话是 AI 对 AI, 不计入「用户会话」统计
    # (主代理派生子代理时该目录才会出现, 不排除会污染 total_sessions 等指标)
    jsonl_files = [
        fp for fp in jsonl_files
        if "subagents" not in os.path.normpath(fp).split(os.sep)
    ]
    sessions_raw = {}
    for fp in jsonl_files:
        sid = os.path.splitext(os.path.basename(fp))[0]
        events = []
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try: events.append(json.loads(line.strip()))
                    except json.JSONDecodeError: pass
        except Exception: pass
        if events: sessions_raw[sid] = events

    sessions = []
    for sid, events in sessions_raw.items():
        msgs = []
        titles = []
        tools = []
        files_touched = []
        timestamps = []
        hours = []
        thinking_count = 0

        for ev in events:
            t = ev.get("type"); ts = ev.get("timestamp","")
            hour = None; dt = None
            if ts:
                try:
                    # jsonl 里是 UTC 时间戳，须转本地时区，否则小时统计整体偏移
                    dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone()
                    hour = dt.hour; timestamps.append(dt); hours.append(hour)
                except: pass

            if t == "ai-title":
                # 标题同样可能有孤立代理字符，过滤后作为散文素材才可用
                titles.append(_SURROGATE_RE.sub("", ev.get("aiTitle","")))
            elif t == "user":
                text = extract_text(ev.get("message",{}).get("content",[]))
                if text: msgs.append({"role":"user","text":text,"timestamp":dt,"hour":hour})
            elif t == "assistant":
                msg = ev.get("message",{})
                think_n = 0
                for c in msg.get("content",[]):
                    if isinstance(c,dict):
                        if c.get("type")=="thinking": think_n += 1
                        elif c.get("type")=="tool_use":
                            tools.append(c.get("name","?"))
                thinking_count += think_n
                # assistant 消息不拆分展示，但计入思考/工具
                msgs.append({"role":"assistant","text":"","timestamp":dt,"hour":hour,"think":think_n})
            elif t == "file-history-delta":
                p = ev.get("trackingPath","")
                if p: files_touched.append(os.path.basename(p))
            elif t == "file-history-snapshot":
                # 新版 Claude Code: snapshot.trackedFileBackups 的键是文件路径
                backups = ev.get("snapshot", {}).get("trackedFileBackups", {})
                if isinstance(backups, dict):
                    for p in backups:
                        if p:
                            files_touched.append(os.path.basename(p))

        if any(m["role"]=="user" for m in msgs):
            sessions.append({
                "id": sid,
                "title": titles[-1] if titles else "无标题",
                "source": "claude-code",
                "messages": msgs,
                "thinking_count": thinking_count,
                "tool_calls": tools,
                "file_touches": files_touched,
                "timestamps": timestamps,
                "hours": hours,
            })
    return sessions


def parse_chatgpt(file_path: str) -> list[dict]:
    """解析 ChatGPT conversations.json → 统一会话列表"""
    with open(file_path, encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    sessions = []
    for conv in raw:
        title = conv.get("title","无标题")
        mapping = conv.get("mapping",{})
        current_node_id = conv.get("current_node","")
        created = conv.get("create_time",0)

        # 从 current_node 回溯到根
        ordered = []
        node_id = current_node_id
        while node_id and node_id in mapping:
            node = mapping[node_id]
            ordered.append(node)
            parent = node.get("parent")
            if parent == node_id: break
            node_id = parent
        ordered.reverse()

        msgs = []
        timestamps = []; hours = []; tools = []
        for node in ordered:
            msg = node.get("message")
            if not msg: continue
            author = msg.get("author",{}).get("role","")
            content = msg.get("content",{})
            parts = content.get("parts",[])
            ctype = content.get("content_type","text")

            # 提取文本
            text_parts = []
            for p in parts:
                if isinstance(p,str): text_parts.append(p)
                elif isinstance(p,dict):
                    # ChatGPT 工具调用: {content_type:"tool_use", ...}
                    if "name" in p:
                        tools.append(p["name"])
                    if "text" in p: text_parts.append(p["text"])
            text = " ".join(text_parts)

            ct = msg.get("create_time")
            dt = None; hour = None
            if ct:
                try:
                    dt = datetime.fromtimestamp(ct, tz=timezone.utc).astimezone()
                    hour = dt.hour; timestamps.append(dt); hours.append(hour)
                except: pass

            if author in ("user","assistant","system") and text:
                msgs.append({"role":author,"text":text,"timestamp":dt,"hour":hour})

        if any(m["role"]=="user" for m in msgs):
            sessions.append({
                "id": conv.get("id","")[:12],
                "title": title,
                "source": "chatgpt",
                "messages": msgs,
                "thinking_count": 0,  # ChatGPT 无思考
                "tool_calls": tools,
                "file_touches": [],
                "timestamps": timestamps,
                "hours": hours,
                "created": created,
            })
    return sessions


def parse_zcode(db_path: str) -> list[dict]:
    """解析 ZCode 会话库 (~/.zcode/cli/db/db.sqlite) → 统一会话列表"""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # 只统计交互会话(排除后台任务), 与 CC 版排除 subagents 同理
        sess_rows = con.execute(
            "SELECT id, title FROM session WHERE task_type='interactive'"
        ).fetchall()
        sessions = []
        for sid, title in sess_rows:
            parts_by_msg = defaultdict(list)
            for mid, pdata in con.execute(
                "SELECT message_id, data FROM part WHERE session_id=? "
                "ORDER BY COALESCE(sequence, rowid)", (sid,),
            ):
                try:
                    parts_by_msg[mid].append(json.loads(pdata))
                except Exception:
                    pass

            msgs, tools, files_touched, timestamps, hours = [], [], [], [], []
            thinking_count = 0
            for mid, ts_ms, mdata in con.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? "
                "ORDER BY COALESCE(sequence, rowid)", (sid,),
            ):
                try:
                    m = json.loads(mdata)
                except Exception:
                    continue
                sem = m.get("semantics") or {}
                if sem.get("uiVisibility") == "hidden":
                    continue
                dt = hour = None
                if ts_ms:
                    try:
                        # 库里是毫秒 epoch, 须转本地时区, 否则小时统计整体偏移
                        dt = datetime.fromtimestamp(ts_ms / 1000).astimezone()
                        hour = dt.hour; timestamps.append(dt); hours.append(hour)
                    except Exception:
                        pass

                kind = sem.get("kind")
                if m.get("role") == "user" and kind == "user_prompt":
                    text = _SURROGATE_RE.sub("", " ".join(
                        p.get("text", "") for p in parts_by_msg.get(mid, [])
                        if isinstance(p, dict) and p.get("type") == "text"
                    ))[:MAX_MSG_CHARS]
                    if text:
                        msgs.append({"role": "user", "text": text, "timestamp": dt, "hour": hour})
                elif m.get("role") == "assistant":
                    think_n = 0
                    for p in parts_by_msg.get(mid, []):
                        if not isinstance(p, dict):
                            continue
                        t = p.get("type")
                        if t == "reasoning":
                            think_n += 1
                        elif t == "tool":
                            tools.append(p.get("tool", "?"))
                            state = p.get("state") or {}
                            inp = state.get("input") or {}
                            fp = inp.get("file_path") or inp.get("path") if isinstance(inp, dict) else None
                            if fp:
                                files_touched.append(os.path.basename(str(fp)))
                    thinking_count += think_n
                    # assistant 消息不拆分展示，但计入思考/工具
                    msgs.append({"role": "assistant", "text": "", "timestamp": dt, "hour": hour, "think": think_n})

            if any(m_["role"] == "user" for m_ in msgs):
                sessions.append({
                    "id": sid,
                    "title": _SURROGATE_RE.sub("", title or "") or "无标题",
                    "source": "zcode",
                    "messages": msgs,
                    "thinking_count": thinking_count,
                    "tool_calls": tools,
                    "file_touches": files_touched,
                    "timestamps": timestamps,
                    "hours": hours,
                })
        return sessions
    finally:
        con.close()


def detect_and_parse(path: str) -> tuple[list[dict], str]:
    """自动检测数据源并解析，返回 (会话列表, 来源说明)"""
    if os.path.isdir(path):
        sessions = parse_claude_code(path)
        if sessions:
            return sessions, f"Claude Code ({len(sessions)} 次会话)"
        # 目录里也可能是 ChatGPT 导出（修复：原代码此处永远不可达）
        cf = os.path.join(path, "conversations.json")
        if os.path.exists(cf):
            try:
                sessions = parse_chatgpt(cf)
                if sessions:
                    return sessions, f"ChatGPT ({len(sessions)} 次会话)"
            except Exception: pass
        return [], ""

    if os.path.isfile(path):
        if path.endswith(".sqlite"):
            try:
                sessions = parse_zcode(path)
                if sessions:
                    return sessions, f"ZCode ({len(sessions)} 次会话)"
            except Exception: pass
        try:
            sessions = parse_chatgpt(path)
            if sessions:
                return sessions, f"ChatGPT ({len(sessions)} 次会话)"
        except Exception: pass

    return [], ""


# ═══════════════════════════════════════════════════
# 统一分析引擎
# ═══════════════════════════════════════════════════

def analyze_sessions(sessions: list[dict]) -> dict:
    """对统一格式的会话列表进行分析，返回报告数据字典"""
    if not sessions: return {"error":"没有有效会话"}

    total_sessions = len(sessions)
    all_msgs = [m for s in sessions for m in s["messages"]]
    user_msgs_all = [m for m in all_msgs if m["role"]=="user"]
    total_user_msgs = len(user_msgs_all)
    total_assistant_msgs = len([m for m in all_msgs if m["role"]=="assistant"])

    if total_user_msgs == 0: return {"error":"没有用户消息"}

    all_user_texts = [m["text"] for m in user_msgs_all]
    all_hours = [h for s in sessions for h in s["hours"]]
    all_timestamps = sorted(t for s in sessions for t in s["timestamps"])
    if not all_timestamps:
        return {"error": "没有可用的时间戳数据"}
    total_thinking = sum(s["thinking_count"] for s in sessions)
    total_tools = sum(len(s["tool_calls"]) for s in sessions)

    # ── 一、全貌 ──
    first_date = all_timestamps[0]; last_date = all_timestamps[-1]
    total_days = (last_date-first_date).days+1
    all_dates = [t.strftime("%Y-%m-%d") for t in all_timestamps]
    unique_dates = len(set(all_dates))
    active_day_ratio = round(unique_dates/total_days*100,1)

    total_chars = sum(len(t) for t in all_user_texts)
    avg_msg_chars = round(total_chars/total_user_msgs,1)
    avg_msgs_per_session = round(total_user_msgs/total_sessions,1)

    sess_lens = sorted(len([m for m in s["messages"] if m["role"]=="user"]) for s in sessions)
    median_len = sess_lens[len(sess_lens)//2] if sess_lens else 0
    max_len = sess_lens[-1] if sess_lens else 0
    min_len = sess_lens[0] if sess_lens else 0

    user_per_day = Counter()
    for m in user_msgs_all:
        ts = m.get("timestamp")
        if ts: user_per_day[ts.strftime("%Y-%m-%d")] += 1
    busiest_day, busiest_day_msgs = user_per_day.most_common(1)[0] if user_per_day else ("N/A",0)
    top_3_days = user_per_day.most_common(3)

    # ── 二、节奏 ──
    hour_dist = Counter(all_hours)
    peak_hour = max(hour_dist,key=hour_dist.get) if hour_dist else 0
    peak_count = hour_dist[peak_hour]
    WEEKDAY_CN = {"Monday":"周一","Tuesday":"周二","Wednesday":"周三",
                  "Thursday":"周四","Friday":"周五","Saturday":"周六","Sunday":"周日"}
    all_weekdays = [t.strftime("%A") for t in all_timestamps]
    weekday_dist = Counter(all_weekdays)
    wd_en = weekday_dist.most_common(1)[0][0] if weekday_dist else ""
    busiest_weekday = WEEKDAY_CN.get(wd_en, wd_en or "N/A")

    def period_pct(s,e):
        n = sum(hour_dist[h] for h in range(s,e))
        return round(n/sum(hour_dist.values())*100,1) if hour_dist else 0
    morning_pct = period_pct(6,12); afternoon_pct = period_pct(12,18)
    evening_pct = period_pct(18,22); late_night_pct = period_pct(22,24)+period_pct(0,6)

    sorted_dates = sorted(set(all_dates))
    max_streak=1; cur=1
    for i in range(1,len(sorted_dates)):
        d1=datetime.strptime(sorted_dates[i-1],"%Y-%m-%d"); d2=datetime.strptime(sorted_dates[i],"%Y-%m-%d")
        cur = cur+1 if (d2-d1).days==1 else 1; max_streak = max(max_streak,cur)
    gaps=[]
    for i in range(1,len(sorted_dates)):
        d1=datetime.strptime(sorted_dates[i-1],"%Y-%m-%d"); d2=datetime.strptime(sorted_dates[i],"%Y-%m-%d")
        gaps.append((d2-d1).days)
    avg_gap = round(sum(gaps)/len(gaps),1) if gaps else 0

    # ── 三、语言 ──
    word_counter = Counter()
    for t in all_user_texts:
        for w in t.split():
            if is_meaningful(w): word_counter[w.lower()] += 1
    top_words = word_counter.most_common(40)

    cn_bigram = Counter()
    for t in all_user_texts:
        for part in re.findall(r'[一-鿿]+',t):
            for i in range(len(part)-1):
                bg = part[i:i+2]
                if is_meaningful_bigram(bg): cn_bigram[bg] += 1
    top_cn = cn_bigram.most_common(30)

    cn_chars = sum(1 for t in all_user_texts for ch in t if "一"<=ch<="鿿")
    en_chars = sum(1 for t in all_user_texts for ch in t if ch.isascii() and ch.isalpha())
    cn_ratio = round(cn_chars/(cn_chars+en_chars)*100,1) if (cn_chars+en_chars)>0 else 0

    # 行为关键词用成词/词边界, 不用单字, 否则「对」「不是」等日常高频字系统性误报
    thank_n = sum(1 for t in all_user_texts if re.search(r"谢谢|感谢|thanks|thank\s*you",t,re.I))
    sorry_n = sum(1 for t in all_user_texts if re.search(r"不对|错了|重新|重来|再试|不行|搞错",t))
    please_n = sum(1 for t in all_user_texts if re.search(r"请|帮|能不能|可不可以|帮忙",t))
    interrupt_n = sum(1 for t in all_user_texts if re.search(r"算了|停下|别跑了|别弄了|等等|\bStop\b",t,re.I))
    confirm_n = sum(1 for t in all_user_texts if re.search(r"好的|OK|没错|嗯|可以|行吧|没问题|对的|说得对",t,re.I))

    # 消息长度趋势: 按时间序首尾各 25% 的消息(不重叠, 数据少也不会首尾交叉),
    # 相对差超过 5% 才算趋势——原「首/末 7 天」在活跃天数不足 14 天时会重叠,
    # 且 0.1% 的微小差异也被判成「变长」, 噪声敏感
    lens_by_time = [len(m["text"]) for m in
                    sorted(user_msgs_all, key=lambda m: m.get("timestamp") or "")]
    n = len(lens_by_time)
    q = max(1, n // 4)
    fw_seg, rw_seg = lens_by_time[:q], lens_by_time[-q:]
    fw_avg = round(sum(fw_seg) / len(fw_seg), 1) if fw_seg else 0
    rw_avg = round(sum(rw_seg) / len(rw_seg), 1) if rw_seg else 0
    if fw_avg <= 0:
        length_trend = "没有明显变化"
    else:
        diff = (rw_avg - fw_avg) / fw_avg
        length_trend = ("变长了" if diff > 0.05
                        else "变短了" if diff < -0.05
                        else "没有明显变化")

    # ── 四、工具 ──
    tool_counter = Counter()
    file_counter = Counter()
    all_think_depths = []
    for s in sessions:
        for tn in s["tool_calls"]: tool_counter[tn] += 1
        for fn in s["file_touches"]: file_counter[fn] += 1
        last_think = 0
        for m in s["messages"]:
            if m["role"]=="assistant" and m.get("think",0)>0:
                all_think_depths.append(m["think"])
                last_think = m["think"]

    tool_breakdown = tool_counter.most_common(15)
    top_tool = tool_breakdown[0] if tool_breakdown else ("N/A",0)
    tool_per_msg = round(total_tools/total_user_msgs,2)
    thinking_per_msg = round(total_thinking/total_user_msgs,2)

    file_type_counter = Counter()
    for fn,cnt in file_counter.items():
        file_type_counter[os.path.splitext(fn)[1] or "(none)"] += cnt
    top_files = file_counter.most_common(12)

    # ── 五、思考 ──
    avg_think = round(sum(all_think_depths)/len(all_think_depths),1) if all_think_depths else 0
    max_think = max(all_think_depths) if all_think_depths else 0
    min_think = min(all_think_depths) if all_think_depths else 0
    think_depth_dist = Counter(all_think_depths)

    deep_hours = Counter()
    for s in sessions:
        for m in s["messages"]:
            if m["role"]=="assistant" and m.get("think",0)>5:
                if m.get("hour") is not None: deep_hours[m["hour"]] += 1

    # ── 六、主题 ──
    title_words = Counter()
    for s in sessions:
        title = s["title"]
        for w in title.split():
            if is_meaningful(w): title_words[w] += 1
        for part in re.findall(r'[一-鿿]+',title):
            for i in range(len(part)-1):
                bg = part[i:i+2]
                if is_meaningful_bigram(bg): title_words[bg] += 1
    recurring_topics = title_words.most_common(12)

    # ── 七、非凡时刻 ──
    # 带标题输出: (id, 标题, 数量)。标题是写作素材——作者要写「最长的那次会话」得知道它在谈什么
    sess_by_len = sorted([(s["id"], s["title"], len([m for m in s["messages"] if m["role"]=="user"])) for s in sessions],key=lambda x:-x[2])
    sess_by_tool = sorted([(s["id"], s["title"], len(s["tool_calls"])) for s in sessions],key=lambda x:-x[2])
    longest_3 = sess_by_len[:3]
    most_tool_3 = sess_by_tool[:3]
    top_session_titles = sess_by_len[:8]

    # ── 来源信息 ──
    sources = Counter(s["source"] for s in sessions)

    return {
        "total_sessions":total_sessions,"total_user_msgs":total_user_msgs,
        "total_assistant_msgs":total_assistant_msgs,"total_thinking":total_thinking,
        "total_tools":total_tools,"total_chars":total_chars,
        "avg_msg_chars":avg_msg_chars,"avg_msgs_per_session":avg_msgs_per_session,
        "median_len":median_len,"max_len":max_len,"min_len":min_len,
        "first_date":first_date.strftime("%Y年%m月%d日"),
        "last_date":last_date.strftime("%Y年%m月%d日"),
        "total_days":total_days,"unique_dates":unique_dates,
        "active_day_ratio":active_day_ratio,
        "busiest_day":busiest_day,"busiest_day_msgs":busiest_day_msgs,
        "total_files_touched":len(file_counter),
        "hour_dist":{h:hour_dist.get(h,0) for h in range(24)},
        "peak_hour":peak_hour,"peak_count":peak_count,
        "morning_pct":morning_pct,"afternoon_pct":afternoon_pct,
        "evening_pct":evening_pct,"late_night_pct":late_night_pct,
        "max_streak":max_streak,"avg_gap":avg_gap,
        "busiest_weekday":busiest_weekday,
        "top_words":top_words,"top_cn":top_cn,"cn_ratio":cn_ratio,
        "thank_n":thank_n,"sorry_n":sorry_n,"please_n":please_n,
        "interrupt_n":interrupt_n,"confirm_n":confirm_n,
        "fw_avg":fw_avg,"rw_avg":rw_avg,"length_trend":length_trend,
        "tool_breakdown":tool_breakdown,"top_tool":top_tool[0],"top_tool_count":top_tool[1],
        "top_files":top_files,"file_type_counter":file_type_counter.most_common(6),
        "tool_per_msg":tool_per_msg,"thinking_per_msg":thinking_per_msg,
        "avg_think":avg_think,"max_think":max_think,"min_think":min_think,
        "think_depth_dist":dict(think_depth_dist.most_common(8)),
        "deep_hours":dict(deep_hours.most_common(6)),
        "recurring_topics":recurring_topics,
        "longest_3":longest_3,"most_tool_3":most_tool_3,
        "top_session_titles":top_session_titles,
        "top_3_days":top_3_days,
        "sources":dict(sources),
    }


# ═══════════════════════════════════════════════════
# 主入口（兼容旧接口）
# ═══════════════════════════════════════════════════

def analyze(project_dir: str | None = None) -> dict:
    """自动检测数据源并分析"""
    sessions, source_label = [], ""
    if project_dir is None:
        # ZCode 侧默认: 优先 ZCode 会话库, 其次 Claude Code, 最后 ChatGPT 导出
        zdb = os.path.expanduser("~/.zcode/cli/db/db.sqlite")
        if os.path.exists(zdb):
            sessions, source_label = detect_and_parse(zdb)
        if not sessions:
            project_dir = os.path.expanduser("~/.claude/projects")
            sessions, source_label = detect_and_parse(project_dir)
    else:
        sessions, source_label = detect_and_parse(project_dir)

    # 如果没找到，尝试 ChatGPT 导出文件
    if not sessions:
        alt_paths = [
            os.path.expanduser("~/Downloads/conversations.json"),
            os.path.expanduser("~/Desktop/conversations.json"),
        ]
        for alt in alt_paths:
            sessions, source_label = detect_and_parse(alt)
            if sessions: break

    if not sessions:
        return {"error": "未找到任何 AI 对话数据。\n支持: ZCode (~/.zcode/cli/db/db.sqlite)、Claude Code (~/.claude/projects) 或 ChatGPT 导出 (conversations.json)"}

    result = analyze_sessions(sessions)
    result["source_label"] = source_label
    return result


# ═══════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    """CLI: python analyze.py [数据路径] → 分析结果 JSON 到 stdout"""
    import sys
    # Windows 管道下 Python 默认按 GBK 编码输出, Claude Code 按 UTF-8 读取
    # 会得到乱码 JSON。必须显式切到 UTF-8。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else None
    data = analyze(path)
    print(json.dumps(data, ensure_ascii=False, indent=2))
