"""agenttime 的回归测试。

    python tests/test_agenttime.py

只依赖标准库。装了 zstandard 才会跑「读 DSH 会话」那部分。
改动 adapter、统计或渲染之后先跑这个——这里每一条都对应一个真实踩过的坑。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import engine  # noqa: E402
import render  # noqa: E402
from adapters.base import LoadReport, Message, Session  # noqa: E402

def _pick_tmp() -> str:
    """找一个真能写的临时目录。

    装在只读位置、或者系统的临时目录本身不可写时，退回到 tests/_tmp。
    """
    forced = os.environ.get("AGENTTIME_TEST_TMP")
    if forced:
        return forced
    try:
        d = tempfile.mkdtemp(prefix="agenttime-test-")
        probe = os.path.join(d, ".probe")
        open(probe, "w").close()
        os.remove(probe)
        return d
    except OSError:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")


TMP = _pick_tmp()
os.makedirs(TMP, exist_ok=True)
TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=TZ)

failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"（期望 {want!r}）"))
    if not ok:
        failures.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"（{detail}）"))
    if not cond:
        failures.append(name)


def session(idx, n_msgs, n_tools, ts=T0, title=None):
    msgs = [Message(text=f"消息{i}", dt=ts + timedelta(minutes=i)) for i in range(n_msgs)]
    return Session(id=f"s{idx}", title=title or f"项目{idx}", source="dsh",
                   msgs=msgs, tools=Counter({"t": n_tools}), reasoning_chars=100)


def reset_tmp():
    for name in os.listdir(TMP):
        p = os.path.join(TMP, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)


# ─────────────────────────── 数字与格式化 ───────────────────────────
print("中文数字：")
for n, want in [(0, "零"), (10, "十"), (11, "十一"), (110, "一百一十"),
                (2000, "两千"), (10001, "一万零一"), (110000, "十一万"),
                (100000000, "一亿"), (100000001, "一亿零一"),
                (1234567890, "十二亿三千四百五十六万七千八百九十")]:
    check(f"cn_num({n})", render.cn_num(n), want)

print("\n格式化：")
check("千分位", render._fmt(16677, ["comma"]), "16,677")
check("千分位不丢小数", render._fmt(82.5, ["comma"]), "82.5")
check("千分位带小数分组", render._fmt(1234.5, ["comma"]), "1,234.5")
check("千分位整数照旧", render._fmt(40.0, ["comma"]), "40")
check("无过滤器保留精度", render._fmt(82.46, []), "82.46")
check("无过滤器去掉 .0", render._fmt(40.0, []), "40")
check("取整四舍五入", render._fmt(82.5, ["round"]), "83")
check("取整舍去小数", render._fmt(82.4, ["round"]), "82")
check("取整不受二进制浮点影响", render._fmt(2.5, ["round"]), "3")
check("XML 转义", render._fmt("a<b>", []), "a&lt;b&gt;")
check("bool", render._fmt(True, []), "true")
check("None 渲染为空", render._fmt(None, []), "")


def fmt_raises(value, filters):
    try:
        render._fmt(value, filters)
    except ValueError:
        return True
    return False


check_true("小数不能直接用中文万数", fmt_raises(82.5, ["cnwan"]))
check_true("小数不能直接用中文万数别名", fmt_raises(82.5, ["cn"]))
check_true("千分位和中文万数不能同时用", fmt_raises(82.5, ["comma", "cnwan"]))
check_true("数字过滤器不能用在文字上", fmt_raises("abc", ["comma"]))
check("先 round 再转中文", render._fmt(82.5, ["round", "cnwan"]), "八十三")
check("先 round 再加千分位", render._fmt(1234567.5, ["round", "comma"]), "1,234,568")
check("中文万数用于整数照旧", render._fmt(40000, ["cnwan"]), "四万")

print("\n取值路径：")
facts = {"a": {"b": 1}, "arr": [[10, 20], [30, 40]], "empty": [], "s": "文本"}
check("字典两点", render.get_path(facts, "a.b"), 1)
check("列表下标", render.get_path(facts, "arr.0.1"), 20)
check("越界返回空", render.get_path(facts, "arr.9.0"), "")
check("负下标返回空", render.get_path(facts, "arr.-1.0"), "")
check("空列表返回空", render.get_path(facts, "empty.0"), "")
check("穿过标量返回空", render.get_path(facts, "s.0"), "")

print("\n占位符替换：")
render.MISSING.clear()
check("带空格也认", render.subst("{ a.b }", facts), "1")
check("带过滤器", render.subst("{ arr.0.1 | comma }", facts), "20")
check("取不到置空", render.subst("[{nope}]", facts), "[]")
check_true("取不到会记账", "nope" in render.MISSING)
check("散文里的花括号原样保留", render.subst("{x: int}", facts), "{x: int}")
check("残留检查不误报", render.LEFTOVER.findall("{x: int}"), [])
check("残留检查抓得到", render.LEFTOVER.findall("{volume.msgs}"), ["{volume.msgs}"])

# ─────────────────────────── 统计 ───────────────────────────
print("\n统计：")
two = [session(0, 10, 1), session(1, 30, 1)]
f = engine.build_facts(two, LoadReport(source="dsh", ok=2))
check("会话数", f["volume"]["sessions"], 2)
check("消息数", f["volume"]["msgs"], 40)
check("来源", f["sources"], {"dsh": 2})
check("平均句数", f["rhythm"]["avg_per_sitting"], 40.0)

# 没有时间戳的消息不该被摊进「一次坐下」的平均值
mixed = [Message(text="有", dt=T0 + timedelta(minutes=i * 5)) for i in range(10)]
mixed += [Message(text="没有", dt=None) for _ in range(10)]
f = engine.build_facts([Session(id="x", title="x", source="dsh", msgs=mixed,
                                tools=Counter())], LoadReport(source="dsh", ok=1))
check("无时间戳不计入平均句数", f["rhythm"]["avg_per_sitting"], 10.0)
check("但计入总量", f["volume"]["msgs"], 20)

# deepest_project 必须能在 projects 列表里找到
many = [session(i, 100 if i else 2, 500 if i == 0 else 1) for i in range(11)]
f = engine.build_facts(many, LoadReport(source="dsh", ok=11))
titles = [p["title"] for p in f["projects"]]
check("项目列表最多 10 条", len(titles), 10)
check_true("deepest 在列表里", f["deepest_project"]["title"] in titles,
           f["deepest_project"]["title"])

print("\n行为关键词：")
for text, want in [("算了，不做了", True), ("等等，我觉得不对", True),
                   ("统计年鉴等等这些数据", False), ("苹果、香蕉等等", False),
                   ("等等我发现了新问题", True), ("先别急着改", True),
                   ("all good", False)]:
    check(f"打断 {text[:10]!r}", bool(engine.RE_INTERRUPT.search(text)), want)
check("致谢", bool(engine.RE_THANK.search("谢谢")), True)
check("确认", bool(engine.RE_CONFIRM.match("好的")), True)
check("确认（长句不算）", bool(engine.RE_CONFIRM.match("好的，接下来做别的")), False)

print("\n边界：")
try:
    engine.build_facts([Session(id="y", title="y", source="dsh",
                                msgs=[Message(text="z", dt=None)])])
    check_true("全无时间戳应报错", False)
except ValueError:
    check_true("全无时间戳应报错", True)
f = engine.build_facts([session(0, 1, 1)], LoadReport(source="dsh", ok=1))
check("单条消息的天数", f["span"]["total_days"], 1)
check("单条消息的连续天", f["rhythm"]["max_streak"], 1)

# 最晚钟点不能被较新的日期覆盖；审批不能靠减法推断拒绝。
late = [Message("a", T0.replace(hour=4, minute=59)),
        Message("b", (T0 + timedelta(days=1)).replace(hour=0, minute=1))]
f = engine.build_facts([Session("late", "late", "dsh", msgs=late,
                               approvals_asked=4, approvals_granted=1,
                               approvals_denied=1, approvals_cancelled=1,
                               approvals_pending=1)])
check("最晚按钟点取值", f["rhythm"]["latest"], "01 月 01 日 04:59")
check("只数明确拒绝", f["agent"]["denied"], 1)
check("取消单独统计", f["agent"]["cancelled"], 1)
check("未决单独统计", f["agent"]["pending"], 1)
span_msgs = [Message("a", T0), Message("b", T0 + timedelta(days=59))]
f = engine.build_facts([Session("span", "span", "dsh", msgs=span_msgs)])
check("月份在统计层产生", f["span"]["months"], 2)
check("封面从 facts 取月份", "这 2 个月" in render._headline(f), True)
check("日期在统计层格式化", f["span"]["first_day"], "2026.01.01")

# ─────────────────────────── 读 DSH 会话 ───────────────────────────
try:
    import zstandard as zstd
except ImportError:
    zstd = None

if zstd is None:
    print("\n（没装 zstandard，跳过会话读取部分）")
else:
    from adapters import dsh

    def ev(text, ts=1767225600000, kind="user"):
        return json.dumps({"type": "user/message", "time": ts,
                           "data": {"source": {"kind": kind},
                                    "content": [{"type": "text", "text": text}]}},
                          ensure_ascii=True)

    def write(lines, cut=0):
        reset_tmp()
        d = os.path.join(TMP, "s1", "sess")
        os.makedirs(d)
        blob = b"".join(zstd.ZstdCompressor().compress(x.encode() + b"\n") for x in lines)
        if cut:
            blob = blob[:-cut]
        open(os.path.join(d, "session.v4.jsonl.zstd"), "wb").write(blob)
        return TMP

    print("\n读会话：")
    write([ev("第一条"), ev("第二条", 1767225600001)])
    s, r = dsh.load(TMP)
    check("正常读两条", sum(len(x.msgs) for x in s), 2)
    check("没有读错", r.read_errors, 0)

    # 一条坏行不能废掉整批数据
    write([ev("好"), "[1,2,3]", ev("也好", 1767225600001)])
    s, r = dsh.load(TMP)
    check("坏行之后仍读到 2 条", sum(len(x.msgs) for x in s), 2)
    check("坏行单独记账", r.malformed_events, 1)

    write([ev("好"), '{"type":"user/message","time":1,"data":"oops"}'])
    check("data 是字符串不炸", sum(len(x.msgs) for x in dsh.load(TMP)[0]), 1)

    write([ev("好"), '{"type":"assistant/message","time":2,"data":{"message":"x"}}'])
    check("message 是字符串不炸", sum(len(x.msgs) for x in dsh.load(TMP)[0]), 1)

    # 写到一半的文件要报出来，而不是当作「就这么多」
    write([ev("一"), ev("二", 1767225600001)], cut=5)
    s, r = dsh.load(TMP)
    check("截断文件仍能读出一部分", sum(len(x.msgs) for x in s), 1)
    check("截断计为读错", r.read_errors, 1)

    write([json.dumps({"type": "session/title", "data": {"title": None}}), ev("你好")])
    check("标题 null 不变成字面量 None", dsh.load(TMP)[0][0].title, "无标题")

    # 系统注入必须挡掉，没有 source 的旧格式保守保留
    write([ev("真人"), ev("系统", 1, kind="system"),
           '{"type":"user/message","time":2,"data":{"content":[{"type":"text","text":"旧格式"}]}}'])
    s, r = dsh.load(TMP)
    check("注入被挡", r.filtered_injected, 1)
    check("旧格式保留", sum(len(x.msgs) for x in s), 2)

if zstd is not None:
    write([ev("正常"), "{bad json}", ev("仍然正常")])
    s, r = dsh.load(TMP)
    check("非法 JSON 后仍可读", sum(len(x.msgs) for x in s), 2)
    check("非法 JSON 单独记账", r.malformed_events, 1)
    check_true("非法 JSON 留有提示", "不是合法 JSON" in r.as_dict()["error_hint"])
    write(["{bad json}"])
    s, r = dsh.load(TMP)
    check("全坏行也记账", r.malformed_events, 1)
    check("全坏行记读取失败", r.read_errors, 1)
    decisions = [json.dumps({"type":"approval/asked", "data":{}}) for _ in range(5)]
    decisions += [json.dumps({"type":"approval/decided", "data":{"outcome":v}})
                  for v in ("allowed-once", "rejected", "cancelled", "future-outcome")]
    write([ev("真人")] + decisions)
    f = engine.build_facts(dsh.load(TMP)[0])
    check("adapter 明确拒绝", f["agent"]["denied"], 1)
    check("adapter 取消", f["agent"]["cancelled"], 1)
    check("adapter 未决", f["agent"]["pending"], 1)
    check("adapter 未知结果", f["agent"]["unknown"], 1)

# ─────────────────────────── 渲染 ───────────────────────────
print("\n渲染：")
RENDER = os.path.join(ROOT, "render.py")


def run_render(narrative, facts=None, template=None):
    reset_tmp()
    fp, np_, op = (os.path.join(TMP, n) for n in ("f.json", "n.json", "o.html"))
    base = {"span": {"first": "2026-01-01 10:00", "last": "2026-01-02 10:00",
                     "total_days": 2, "months": 1, "first_day": "2026.01.01", "last_day": "2026.01.02"},
            "volume": {"sessions": 1, "msgs": 1, "chars": 1, "avg_chars": 1.0},
            "sources": {"dsh": 1}, "quality": {}}
    json.dump(facts if facts is not None else base,
              open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    open(np_, "w", encoding="utf-8").write(
        narrative if isinstance(narrative, str) else json.dumps(narrative, ensure_ascii=False))
    argv = [sys.executable, RENDER, fp, np_]
    if template == "<broken>":
        # 造一个缺 <!-- CARDS --> 的模板（必须放在 reset 之后，否则会被清掉）
        template = os.path.join(TMP, "broken.html")
        base_tpl = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read()
        open(template, "w", encoding="utf-8").write(base_tpl.replace("<!-- CARDS -->", ""))
    if template:
        argv.append(template)
    argv += ["--out", op]
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = open(op, encoding="utf-8").read() if os.path.exists(op) else None
    return p.returncode, p.stdout + p.stderr, out


rc, log, out = run_render({"cards": [{"idx": "01", "kicker": "k", "num": "{volume.msgs}",
                                      "unit": "句", "fact": "<b>{volume.msgs}</b>"}],
                           "essay": ["第一句", "第二句"]})
check("正常渲染 rc", rc, 0)
check("卡片数", (out or "").count('<section class="card"'), 1)
check("散文段数", (out or "").count("<p>第一句</p>"), 1)
check("说字符不说字节", "字节" in log, False)

for label, nar in [("cards 是字符串", {"cards": "abc"}),
                   ("essay 是字符串", {"cards": [], "essay": "整段"}),
                   ("essay 元素是数字", {"cards": [], "essay": [1]}),
                   ("card 元素是字符串", {"cards": ["x"]}),
                   ("chrome 是字符串", {"cards": [], "essay": ["x"], "chrome": "zh"}),
                   ("两者都空", {"cards": []})]:
    rc, log, _ = run_render(nar)
    check_true(f"{label} 要拦住", rc != 0 and "Traceback" not in log, log.strip()[:60])

rc, log, out = run_render({"cards": [], "essay": ["x"],
                           "chrome": {"headline": "<em>甲</em> & 乙<br>丙",
                                      "footer": "<img src=x>"}})
check("chrome 转义", "&lt;em&gt;" in (out or ""), True)
check("headline 的 <br> 保留", "&lt;em&gt;甲&lt;/em&gt; &amp; 乙<br>丙" in (out or ""), True)
check("footer 转义", "&lt;img src=x&gt;" in (out or ""), True)

rc, log, _ = run_render({"cards": [], "essay": ["x"]},
                        facts=[1, 2, 3])
check_true("facts 不是对象要拦住", rc != 0 and "Traceback" not in log, log.strip()[:60])

rc, log, _ = run_render({"cards": [], "essay": ["x"]}, template="<broken>")
check_true("模板缺标记要停下", rc != 0 and "CARDS" in log, log.strip()[:80])

# 数字边界、HTML 安全与 chrome 的集成回归。
for field, value in (("num", "999999"), ("fact", "999999 次"),
                     ("fact", "一百次"), ("fact", "<b>一百</b>"),
                     ("num", "两"), ("unit", "3 次")):
    rc, log, out = run_render({"cards": [{"idx":"01", "kicker":"k", "aside":"x", "num":"{volume.msgs}", "unit":"句", field:value}], "essay":[]})
    check_true(f"拦住手写数字 {field}/{value}", rc != 0 and out is None)
rc, log, out = run_render({"essay":["999999"]})
check_true("散文手写数字被拦住", rc != 0 and out is None)
rc, log, out = run_render({"essay":["这些天，一些事，一起做完。第一句话还在。"]})
check("普通中文叙述不误拦", rc, 0)
rc, log, out = run_render({"essay":["x"], "chrome":{"title":"999"}})
check_true("chrome 手写数字被拦住", rc != 0 and out is None)
rc, log, out = run_render({"essay":["<script>alert(true)</script><b>x</b>"],
    "cards":[{"idx":"01","kicker":"k","fact":'<img src="https://example.invalid/leak" onerror="alert(true)"><b>x</b>'}]})
check("危险标签不进入 DOM", "<script>alert(true)" in (out or ""), False)
check("远程图片不进入 DOM", '<img src="https://' in (out or ""), False)
check("加粗仍保留", "<b>x</b>" in (out or ""), True)
check_true("危险标签按文字显示", "&lt;script&gt;" in (out or ""))
check("facts 中的标签不能变活", render.subst("{text}", {"text":"<b>x</b>"}), "&lt;b&gt;x&lt;/b&gt;")
check("HTML 实体不二次激活", render.subst("&lt;img src=x&gt;", {}), "&amp;lt;img src=x&amp;gt;")
rc, log, out = run_render({"cards":[{"idx":"01","kicker":"k","aside":"x","num":"{volume.msgs}","unit":"<&"}]})
check_true("单位只转义一次", '<span class="unit">&lt;&amp;</span>' in (out or ""))
rc, log, out = run_render({"essay":["x"], "chrome":{
    "headline":"{volume.msgs}<br>messages", "title":"{volume.msgs} messages",
    "turn":"{volume.msgs} messages", "footer":"{volume.msgs} messages"}})
check("chrome 占位符全部替换", "{volume.msgs}" in (out or ""), False)
check_true("chrome 标题替换", "<title>1 messages</title>" in (out or ""))
check_true("chrome 换行保留", "1<br>messages" in (out or ""))
rc, log, out = run_render({"essay":["x"],"chrome":{"headline":"x", "title":"{missing.title}"}})
check_true("浏览器标题缺值也会停止", rc != 0 and out is None)
rc, log, out = run_render({"essay":["{missing.value}"]})
check_true("缺 facts 停止而非补数", rc != 0 and out is None)
old = {"span":{"first":"2026-01-01", "last":"2026-03-01", "total_days":60}}
rc, log, out = run_render({"essay":["x"]}, facts=old)
check_true("旧 facts 不由渲染补算月份", rc != 0 and out is None)
rc, log, out = run_render({"cards":[{"idx":"01","kicker":"k","aside":"x","list_from":"projects"}]}, facts={
    "span":{"total_days":60,"months":2,"first_day":"a","last_day":"b"},
    "projects":[{"title":"p","start":"a"}]})
check_true("列表缺计数不补零", rc != 0 and out is None)

# 异常输入和文件发布的集成回归，必须进入标准验证入口。
p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "test_boundaries.py")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check_true("异常输入与原子发布回归", p.returncode == 0, p.stdout + p.stderr)

# 安装器路径保护：用 mock 拦截所有写入和删除。
if os.name == "nt":
    shell = shutil.which("powershell")
    if shell:
        p = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", os.path.join(ROOT, "tests", "test_install.ps1")],
                           capture_output=True, text=True, errors="replace")
        check_true("Windows 安装路径保护", p.returncode == 0, p.stdout + p.stderr)
else:
    shell = shutil.which("bash")
    if shell:
        p = subprocess.run([shell, os.path.join(ROOT, "tests", "test_install.sh")],
                           capture_output=True, text=True, errors="replace")
        check_true("bash 安装路径保护", p.returncode == 0, p.stdout + p.stderr)

# ─────────────────────────── 汇总 ───────────────────────────
shutil.rmtree(TMP, ignore_errors=True)
print()
if failures:
    print(f"失败 {len(failures)} 项：" + "、".join(failures))
    raise SystemExit(1)
print("全部通过")
raise SystemExit(0)

