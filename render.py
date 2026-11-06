"""facts.json + narrative.json → 单文件 HTML

叙事和渲染分离：narrative.json 只放文字，数字用 {facts 路径} 占位，
渲染时从 facts.json 取值并格式化。这样文案里的数字永远和数据一致。

用法：
    python render.py                      # 默认 facts.json / narrative.json / template.html
    python render.py --out D:\\x.html      # 指定输出
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fileio import atomic_write_text
from validation import check_facts, check_values

# 容忍花括号和竖线两侧的空格：{volume.msgs | comma} 和 { volume.msgs } 都认。
# 不写死成紧凑形式，是因为这类空格一旦漏掉，占位符会原样出现在报告里，
# 而且不会进 MISSING——静默出错最难查。
PLACE = re.compile(r"\{\s*([A-Za-z_][\w.\-]*)\s*((?:\|\s*[A-Za-z]+\s*)*)\}")

# 渲染完之后仍像占位符的片段，用来兜底报警。只认「像 facts 路径」的写法——
# 散文里正常出现的 {x: int}、{a+b} 不该被报成残留占位符。
LEFTOVER = re.compile(r"\{\s*[A-Za-z_][\w.\-]*(?:\s*\|\s*[a-z]+)*\s*\}")

FILTERS = {"comma", "round", "cn", "cnwan"}
CANDIDATE = re.compile(r"\{\s*[A-Za-z_][\w.\-]*(?:\s*\|[^{}]*)?\s*\}")


def _placeholder_problem(text):
    for match in re.finditer(r"\{[^{}\n]*\}", text):
        parsed = PLACE.fullmatch(match.group())
        if parsed is None:
            if CANDIDATE.fullmatch(match.group()) or re.match(r"\{\s*[A-Za-z_]\w*\s*[.\[|]", match.group()):
                return "占位符格式不对，请使用 {facts.路径|过滤器}"
            continue  # 正常散文里的 {x: int}、{a+b} 不是占位符。
        filters = [part.strip() for part in parsed.group(2).split("|") if part.strip()]
        if any(part not in FILTERS for part in filters):
            return "占位符使用了不支持的过滤器（可用 comma、round、cn、cnwan）"
    return ""


# 模板里必须先存在的槽位。拼接靠 str.replace，标记不在就等于内容被安静丢掉。
MARKERS = ("<!-- LANG -->", "<!-- TITLE -->", "<!-- HEADLINE -->", "<!-- RANGE -->",
           "<!-- TURN -->", "<!-- CARDS -->", "<!-- ESSAY -->", "<!-- FOOTER -->")


def get_path(data, path):
    cur = data
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key, "")
        elif isinstance(cur, (list, tuple)):
            if key.startswith("-"):
                # 负下标会安静地取到末尾，笔误写成 -1 看起来像正常取值
                return ""
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
    return cur


_CN_D = "零一二三四五六七八九"


def cn_num(n: int, standalone: bool = True) -> str:
    """整数转中文，散文中「十一万」「两千四百八十一万」这类读感用。

    `standalone=False` 用于百/千后面的位置：那里 10 要写「一十」
    （一百一十），单独说的时候才是「十」。万位和亿位的乘数用
    standalone=True —— 110000 是「十一万」，不是「一十一万」。
    """
    n = int(n)
    if n < 0:
        return "负" + cn_num(-n, standalone)
    if n < 10:
        return _CN_D[n]
    if n < 20:
        return ("十" if standalone else "一十") + (_CN_D[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_D[n // 10] + "十" + (_CN_D[n % 10] if n % 10 else "")
    if n < 1000:
        s = _CN_D[n // 100] + "百"
        r = n % 100
        if r == 0:
            return s
        return s + ("零" + cn_num(r, False) if r < 10 else cn_num(r, False))
    if n < 10000:
        s = ("两千" if n // 1000 == 2 else _CN_D[n // 1000] + "千")
        r = n % 1000
        if r == 0:
            return s
        return s + ("零" + cn_num(r, False) if r < 100 else cn_num(r, False))
    if n < 100000000:
        s = cn_num(n // 10000) + "万"
        r = n % 10000
        if r == 0:
            return s
        return s + ("零" + cn_num(r, False) if r < 1000 else cn_num(r, False))
    # 超过一亿：思考量大的用户会走到这里
    s = cn_num(n // 100000000) + "亿"
    r = n % 100000000
    if r == 0:
        return s
    return s + ("零" + cn_num(r, False) if r < 10000000 else cn_num(r, False))


def _round_half_up(val: float) -> int:
    """四舍五入。不用内置 round()：它是银行家舍入，82.5 会变成 82。

    用 to_integral_value 而不是 quantize：quantize 受 Decimal 默认精度（28 位）
    限制，1e30 这种有限但很大的数会抛 InvalidOperation，一路炸成 traceback。
    """
    try:
        return int(Decimal(str(val)).to_integral_value(rounding=ROUND_HALF_UP))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"数值 {val} 无法取整（{exc.__class__.__name__}）") from None


def _drop_zero_fraction(text: str) -> str:
    """40.0 显示成 40；其余小数原样保留，不补也不截位。"""
    return text[:-2] if text.endswith(".0") else text


def _plain(val) -> str:
    """不加过滤器时的文字：整数照写，小数保留原值，只去掉纯整数的 .0。"""
    if isinstance(val, float):
        return _drop_zero_fraction(f"{val}")
    return str(val)


def _fmt(val, filters):
    """按过滤器把值变成文字。类型或组合不合规就报错，不静默忽略。

    `{x|comma}` 遇到小数曾经悄悄取整、`{x|round|cnwan}` 曾经把中文那一步吞掉，
    都是读者看不出数字被动过的那类错——宁可停下，也不猜作者想要哪一种。
    """
    if val is None:
        return ""
    names = list(filters)
    if isinstance(val, bool):
        # 布尔值原样显示 true/false，但它不是数字，不能被 round/comma 处理
        if names:
            raise ValueError("布尔值不能用数字过滤器")
        return html.escape(str(val).lower())
    if not names:
        return html.escape(_plain(val))
    if not isinstance(val, (int, float)):
        raise ValueError(f"数字过滤器不能用在 {type(val).__name__} 上")

    wants_cn = "cn" in names or "cnwan" in names
    if wants_cn and "comma" in names:
        raise ValueError("千分位和中文万数不能同时用，只能选一个")

    number = val
    if "round" in names and isinstance(number, float):
        number = _round_half_up(number)

    if wants_cn:
        if not isinstance(number, int):
            raise ValueError(f"中文万数只能用于整数，当前是 {number}，先加 |round")
        return html.escape(cn_num(number))
    if "comma" in names:
        # 千分位只加分隔符，不改数值：82.5 → 82.5，1234.5 → 1,234.5
        return html.escape(_drop_zero_fraction(f"{number:,}"))
    return html.escape(_plain(number))


MISSING = set()


def subst(tpl, facts, allow_b=True, allow_br=False):
    """把 {path} / {path|comma} / {path|round} / {path|cnwan} 换成 facts 里的值。"""
    problem = _placeholder_problem(tpl)
    if problem:
        raise ValueError(problem)
    def repl(m):
        path = m.group(1)
        val = get_path(facts, path)
        # 空列表 / 空字典同样算「取不到」：直接渲染会变成 [] 或 {} 出现在正文里
        if val is None or val == "" or val == [] or val == {}:
            MISSING.add(path)
            return ""
        filters = [f.strip() for f in (m.group(2) or "").split("|") if f.strip()]
        if isinstance(val, (dict, list, tuple)):
            raise ValueError(f"{path} 应该指向文字或数值，不能是对象或数组")
        try:
            return _fmt(val, filters)
        except ValueError as exc:
            # 报错要能照着改：路径、用了哪些过滤器、为什么不行，一句里说清
            raise ValueError(f"{path}（{'|'.join(filters)}）：{exc}") from None
    # 先转义文字片段，再插入已经转义的 facts；绝不把 facts 里的标签恢复成 HTML。
    parts, pos = [], 0
    for match in PLACE.finditer(tpl):
        parts.append(_esc(tpl[pos:match.start()], allow_br=allow_br, allow_b=allow_b))
        parts.append(repl(match))
        pos = match.end()
    parts.append(_esc(tpl[pos:], allow_br=allow_br, allow_b=allow_b))
    return "".join(parts)


def _esc(value, allow_br: bool = False, allow_b: bool = False) -> str:
    """仅恢复无属性的排版标签；其余标签和属性全部作为文字显示。"""
    text = html.escape(str(value), quote=True)
    if allow_br:
        text = text.replace("&lt;br&gt;", "<br>")
    if allow_b:
        text = text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return text


# idx 是版式编号，不是统计值。文字中的数值必须改用 facts 占位符。
# 中文只检查明确数量表达，避免把「一些」「一起」等普通词误当成统计。
LITERAL_NUMBER = re.compile(
    r"\d|[零〇一二三四五六七八九两]*[十百千万亿][零〇一二三四五六七八九十百千万亿两]*"
    r"(?=个|次|句|场|天|年|月|周|小时|分钟|秒|字|倍|点|$)"
    r"|[零〇三四五六七八九]+(?=个|次|句|场|天|年|月|周|小时|分钟|秒|字|倍|点)")


def _text_problem(value, label):
    if not isinstance(value, str):
        return f"{label} 应该是字符串"
    problem = _placeholder_problem(value)
    if problem:
        return f"{label}：{problem}"
    literal = html.unescape(PLACE.sub("", value))
    literal = re.sub(r"</?b>|<br>", "", literal)
    if LITERAL_NUMBER.search(literal):
        return f"{label} 含有手写数字，请改用 facts 占位符"
    return ""


def _check_narrative(narrative: dict) -> str:
    """把叙事里的类型错误挡在这里，返回一句能照着改的说明。

    不挡的话：`cards` 写成字符串会逐字符生成卡片，`essay` 写成字符串会
    一字一段，两个都是 rc=0 的静默错报告，比报错难查得多。
    """
    problem = check_values(narrative)
    if problem:
        return problem
    cards = narrative.get("cards")
    essay = narrative.get("essay")
    chrome = narrative.get("chrome")
    if cards is None and essay is None:
        return "既没有 cards 也没有 essay"
    if cards is not None:
        if not isinstance(cards, list):
            return f"cards 应该是数组，现在是 {type(cards).__name__}"
        indices = set()
        for i, card in enumerate(cards, 1):
            if not isinstance(card, dict):
                return f"cards 第 {i} 项应该是对象，现在是 {type(card).__name__}"
            for key in ("idx", "kicker"):
                if key not in card:
                    return f"cards 第 {i} 项缺少 {key}"
            if not isinstance(card["idx"], str) or not re.fullmatch(r"[0-9]{1,3}", card["idx"]):
                return f"cards 第 {i} 项的 idx 应该是卡片编号"
            if card["idx"] in indices:
                return f"cards 第 {i} 项的 idx 重复"
            indices.add(card["idx"])
            if not isinstance(card["kicker"], str) or not card["kicker"].strip():
                return f"cards 第 {i} 项的 kicker 不能为空"
            if not any(isinstance(card.get(key), str) and card[key].strip() for key in ("fact", "aside")):
                return f"cards 第 {i} 项需要非空 fact 或 aside"
            if "num" in card and "unit" not in card:
                return f"cards 第 {i} 项缺少 unit"
            for key in ("kicker", "num", "unit", "fact", "aside"):
                if key in card:
                    problem = _text_problem(card[key], f"cards 第 {i} 项的 {key}")
                    if problem:
                        return problem
                    if key == "num" and not PLACE.fullmatch(card[key].strip()):
                        return f"cards 第 {i} 项的 num 必须是一个 facts 占位符"
            for key in ("list_from", "chips_from"):
                if key in card and not isinstance(card[key], str):
                    return f"cards 第 {i} 项的 {key} 应该是 facts 路径"
    if essay is not None:
        if not isinstance(essay, list):
            return f"essay 应该是数组（一个元素一段），现在是 {type(essay).__name__}"
        for i, para in enumerate(essay, 1):
            if not isinstance(para, str):
                return f"essay 第 {i} 段应该是字符串，现在是 {type(para).__name__}"
            problem = _text_problem(para, f"essay 第 {i} 段")
            if problem:
                return problem
    if chrome is not None and not isinstance(chrome, dict):
        return f"chrome 应该是对象，现在是 {type(chrome).__name__}"
    for key, value in (chrome or {}).items():
        if key in ("title", "headline", "turn", "footer"):
            problem = _text_problem(value, f"chrome.{key}")
            if problem:
                return problem
    if not cards and not essay:
        return "cards 和 essay 都是空的，报告只剩一个封面"
    return ""


def _num_block(num, unit):
    unit = str(unit) if unit else ""
    # num 和 unit 都已通过 subst 转义。
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    return f'  <div class="num">{num}{unit_html}</div>'


def card_html(card, facts):
    idx = html.escape(str(card.get("idx", "")))
    parts = [f'<section class="card" id="c{idx}">',
             f'  <div class="idx">{idx}</div>',
             f'  <p class="kicker">{subst(str(card.get("kicker", "")), facts)}</p>']

    if "num" in card:
        parts.append(_num_block(subst(str(card["num"]), facts),
                                subst(card.get("unit", ""), facts, allow_b=False)))

    if card.get("fact"):
        parts.append(f'  <p class="fact">{subst(card["fact"], facts)}</p>')

    if card.get("list_from"):
        source = get_path(facts, card["list_from"])
        if not isinstance(source, (list, tuple)) or not source:
            MISSING.add(card["list_from"])
        rows = []
        for i, p in enumerate(source if isinstance(source, (list, tuple)) else []):
            if not isinstance(p, dict):
                raise ValueError(f"{card['list_from']}.{i} 应该是对象")
            prefix = card["list_from"] + f".{i}"
            title = subst("{" + prefix + ".title}", facts, allow_b=False)
            date = subst("{" + prefix + ".start}", facts, allow_b=False)
            count = subst("{" + prefix + ".n}", facts, allow_b=False)
            rows.append(f'    <li><span class="t">{title}</span>'
                        f'<span class="m">{date} · {count} 句</span></li>')
        parts.append('  <ul class="lines">\n' + "\n".join(rows) + "\n  </ul>")

    if card.get("chips_from"):
        raw = get_path(facts, card["chips_from"])
        if not isinstance(raw, (list, tuple)):
            raw = []
        if not raw:
            MISSING.add(card["chips_from"])
        words = []
        for i, word in enumerate(raw[:10]):
            path = card["chips_from"] + f".{i}"
            if isinstance(word, (list, tuple)):
                path += ".0"
            words.append(subst("{" + path + "}", facts, allow_b=False))
        chips = "".join(f"<span>{word}</span>" for word in words)
        parts.append(f'  <div class="chips">{chips}</div>')

    if card.get("aside"):
        parts.append(f'  <p class="aside">{subst(card["aside"], facts)}</p>')

    parts.append("</section>")
    return "\n".join(parts)


def _headline(facts) -> str:
    """封面标题。按真实跨度说「这些天 / 这几个月 / 这一年多」，
    不写死个月数——别人可能只用了两周，也可能用了两年。"""
    span = facts.get("span", {}) or {}
    days = span.get("total_days")
    months = span.get("months")
    if not isinstance(days, (int, float)) or not isinstance(months, (int, float)):
        MISSING.add("span.months" if months is None else "span.total_days")
        return ""
    if days <= 45:
        head = "这些天"
    elif months <= 12:
        head = "这 {span.months} 个月"
    else:
        head = "这一年多"
    return subst(head + "的<br>一些记录", facts, allow_b=False, allow_br=True)


def _daterange(facts) -> str:
    return subst("{span.first_day} — {span.last_day}", facts, allow_b=False)


_SOURCE_NAMES = {
    "dsh": "DeepSeek Harness",
    "claude_code": "Claude Code",
    "codex": "Codex",
    "zcode": "ZCode",
}


def _source_line(facts) -> str:
    """页脚那半句话：这份报告到底读了哪些 agent。

    合并多个源时，读者需要知道覆盖范围——否则一份报告看起来像
    「你这段时间什么都没干」，而其实只是别的 agent 没接进来。
    """
    src = facts.get("sources") or {}
    if not src:
        return "本机对话记录"
    parts = [_esc(_SOURCE_NAMES.get(name, name)) + " 的 "
             + subst("{sources." + name + "}", facts, allow_b=False) + " 个会话"
             for name, n in sorted(src.items(), key=lambda kv: -kv[1])]
    return "、".join(parts)


def default_out() -> str:
    """文件名带时分秒，同一天跑几次、几个 agent 各出一份都分得开。

    桌面不存在时（Linux 上很常见）直接落在当前目录，不走「写失败再回退」
    那条路——否则用户会看到一条其实不算错误的 warn。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"agenttime_report_{stamp}.html"
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.join(desktop if os.path.isdir(desktop) else os.getcwd(), name)


def write_out(text: str, path: str, allow_overwrite: bool = True) -> str:
    """显式路径失败就停止；默认路径可安全回退，始终原子发布。"""
    try:
        return atomic_write_text(path, text, overwrite=allow_overwrite)
    except (OSError, UnicodeError, ValueError) as exc:
        if allow_overwrite:
            print(f"[error] 无法写入 {path}（{exc.__class__.__name__}），未改写其他路径。")
            return ""
        fallback = os.path.abspath(os.path.basename(path))
        try:
            final = atomic_write_text(fallback, text, overwrite=False)
        except (OSError, UnicodeError, ValueError) as exc2:
            print(f"[error] {path} 写不进去（{exc.__class__.__name__}），"
                  f"回退到 {fallback} 也失败（{exc2.__class__.__name__}）。")
            print("换一个 --out 目录再试。")
            return ""
        print(f"[warn] 无法写入 {path}（{exc.__class__.__name__}），已改写到 {final}")
        return final


def _same_path(a, b):
    try:
        if os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)):
            return True
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    MISSING.clear()   # 同一个进程里调第二次时，别把上一次的缺失也报出来

    out_path = None
    args = []
    i = 1
    while i < len(argv):
        if argv[i] == "--out":
            if i + 1 >= len(argv):
                print("--out 后面要跟一个路径。")
                return 1
            out_path = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1

    facts_path = args[0] if len(args) > 0 else "facts.json"
    nar_path = args[1] if len(args) > 1 else "narrative.json"
    tpl_path = args[2] if len(args) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "template.html")

    try:
        with open(facts_path, encoding="utf-8") as fh:
            facts = json.load(fh)
    except FileNotFoundError:
        print(f"找不到 facts 文件：{facts_path}")
        print("先跑 `python cli.py scan --out facts.json`。")
        return 1
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"读不了 {facts_path}：{exc}")
        return 1

    if not isinstance(facts, dict):
        print(f"{facts_path} 的顶层应该是一个对象，现在是 {type(facts).__name__}。")
        print("它应该是 `python cli.py scan --out facts.json` 的输出。")
        return 1

    try:
        with open(nar_path, encoding="utf-8") as fh:
            narrative = json.load(fh)
    except FileNotFoundError:
        print(f"找不到叙事文件：{nar_path}")
        print("照 narrative.example.json 的样子写一份。")
        return 1
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"读不了 {nar_path}：{exc}")
        return 1

    if not isinstance(narrative, dict):
        print(f"{nar_path} 的顶层应该是一个对象，现在是 {type(narrative).__name__}。")
        return 1

    try:
        with open(tpl_path, encoding="utf-8") as fh:
            tpl = fh.read()
    except (OSError, UnicodeError) as exc:
        print(f"读不了模板 {tpl_path}：{exc}")
        return 1

    absent = [m for m in MARKERS if m not in tpl]
    if absent:
        print(f"模板 {tpl_path} 缺这些标记：{'、'.join(absent)}")
        print("缺了的话对应内容会被直接丢掉，所以先停下来。")
        return 1

    problem = check_facts(facts)
    if problem:
        print(f"{facts_path} 数据不对：{problem}。")
        return 1
    if out_path and any(_same_path(out_path, path) for path in (facts_path, nar_path, tpl_path)):
        print("[error] --out 不能覆盖 facts、narrative 或模板输入文件。")
        return 1
    problem = _check_narrative(narrative)
    if problem:
        print(f"{nar_path} 结构不对：{problem}。")
        print("照 narrative.example.json 的样子写：一个元素一段，卡片是对象。")
        return 1

    try:
        cards_src = narrative.get("cards") or []
        essay_src = narrative.get("essay") or []
        cards = "\n\n".join(card_html(c, facts) for c in cards_src)
        essay = "\n".join(f"<p>{subst(p, facts)}</p>" for p in essay_src)

        # 模板里那几句固定文案（标题、转场、页脚）默认是中文。报告的语种要跟数据一致，
        # 英文数据就由 narrative.chrome 覆盖掉；不写 chrome 就保持中文默认。
        chrome = narrative.get("chrome") or {}
        lang = _esc(chrome.get("lang") or "zh-CN")
        if chrome.get("headline"):
            headline = subst(chrome["headline"], facts, allow_b=False, allow_br=True)
        else:
            headline = _headline(facts)
        # 标题里不能留 <br>，也不该留下它换来的那个空格
        title = subst(chrome["title"], facts, allow_b=False) if chrome.get("title") else headline.replace("<br>", "")
        turn = subst(chrome.get("turn") or "翻到这里，剩下的不是数字了", facts, allow_b=False)
        if chrome.get("footer"):
            footer = subst(chrome["footer"], facts, allow_b=False)
        else:
            footer = (f"数据来自 {_source_line(facts)}，全部在本地读取。"
                      "系统注入的上下文、指令与技能目录不计入。")

        page = (tpl.replace("<!-- LANG -->", lang)
                   .replace("<!-- TITLE -->", title)
                   .replace("<!-- HEADLINE -->", headline)
                   .replace("<!-- RANGE -->", _daterange(facts))
                   .replace("<!-- TURN -->", turn)
                   .replace("<!-- FOOTER -->", footer)
                   .replace("<!-- CARDS -->", cards)
                   .replace("<!-- ESSAY -->", essay))
    except (ValueError, TypeError, OverflowError) as exc:
        print(f"[error] 无法渲染：{exc}")
        return 1
    if MISSING:
        print("[error] 这些占位符在 facts.json 里找不到，未生成报告："
              + "、".join(sorted(MISSING)))
        return 1
    # 没能替换的占位符会原样留在报告里。MISSING 抓不到这种（根本没匹配上），
    # 所以渲染完再扫一遍正文，宁可多报一句也别让 {xxx} 出现在读者眼前。
    leftover = sorted(set(LEFTOVER.findall(
        "\n".join([cards, essay, title, headline, turn, footer]))))
    if leftover:
        print("[warn] 这些看着像占位符但没替换成功（检查花括号和拼写）："
              + "、".join(leftover[:8]) + ("…" if len(leftover) > 8 else ""))
    # 用户明确指定 --out 时可以覆盖；用默认文件名时一份都不覆盖
    final = write_out(page, out_path or default_out(),
                      allow_overwrite=bool(out_path))
    if not final:
        return 1
    print(f"写入 {final}（{len(page):,} 字符，{len(cards_src)} 张卡）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
