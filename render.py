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

# 容忍花括号和竖线两侧的空格：{volume.msgs | comma} 和 { volume.msgs } 都认。
# 不写死成紧凑形式，是因为这类空格一旦漏掉，占位符会原样出现在报告里，
# 而且不会进 MISSING——静默出错最难查。
PLACE = re.compile(r"\{\s*([A-Za-z_][\w.\-]*)\s*((?:\|\s*[a-z]+\s*)*)\}")

# 渲染完之后仍像占位符的片段，用来兜底报警
LEFTOVER = re.compile(r"\{[^{}\n]{1,60}\}")


def get_path(data, path):
    cur = data
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key, "")
        elif isinstance(cur, (list, tuple)):
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


def _fmt(val, filters):
    if isinstance(val, bool) or val is None:
        s = "" if val is None else str(val).lower()
    elif isinstance(val, int):
        if "cnwan" in filters or "cn" in filters:
            # 交给 cn_num 自己分段：它认得亿，外层再 //10000 会把亿说成万
            s = cn_num(val)
        elif "comma" in filters:
            s = f"{val:,}"
        else:
            s = str(val)
    elif isinstance(val, float):
        if "round" in filters:
            s = str(round(val))
        elif "comma" in filters:
            s = f"{round(val):,}"
        else:
            s = f"{val:.1f}".rstrip("0").rstrip(".")
    else:
        s = str(val)
    return html.escape(s)


MISSING = set()


def subst(tpl, facts):
    """把 {path} / {path|comma} / {path|round} / {path|cnwan} 换成 facts 里的值。"""
    def repl(m):
        path = m.group(1)
        val = get_path(facts, path)
        # 空列表 / 空字典同样算「取不到」：直接渲染会变成 [] 或 {} 出现在正文里
        if val is None or val == "" or val == [] or val == {}:
            MISSING.add(path)
            return ""
        filters = [f.strip() for f in (m.group(2) or "").split("|") if f.strip()]
        return _fmt(val, filters)
    return PLACE.sub(repl, tpl)


def _num_block(num, unit):
    unit = str(unit) if unit else ""
    unit_html = f'<span class="unit">{html.escape(unit)}</span>' if unit else ""
    return f'  <div class="num">{num}{unit_html}</div>'


def card_html(card, facts):
    idx = html.escape(str(card.get("idx", "")))
    parts = [f'<section class="card" id="c{idx}">',
             f'  <div class="idx">{idx}</div>',
             f'  <p class="kicker">{subst(str(card.get("kicker", "")), facts)}</p>']

    if "num" in card:
        parts.append(_num_block(subst(str(card["num"]), facts), card.get("unit", "")))

    if card.get("fact"):
        parts.append(f'  <p class="fact">{subst(card["fact"], facts)}</p>')

    if card.get("list_from"):
        rows = []
        for p in get_path(facts, card["list_from"]) or []:
            rows.append(
                f'    <li><span class="t">{html.escape(str(p.get("title", "")))}</span>'
                f'<span class="m">{p.get("start", "")} · {p.get("n", 0)} 句</span></li>'
            )
        parts.append('  <ul class="lines">\n' + "\n".join(rows) + "\n  </ul>")

    if card.get("chips_from"):
        raw = get_path(facts, card["chips_from"]) or []
        words = [w[0] if isinstance(w, (list, tuple)) else str(w) for w in raw][:10]
        chips = "".join(f"<span>{html.escape(w)}</span>" for w in words)
        parts.append(f'  <div class="chips">{chips}</div>')

    if card.get("aside"):
        parts.append(f'  <p class="aside">{subst(card["aside"], facts)}</p>')

    parts.append("</section>")
    return "\n".join(parts)


def _headline(facts) -> str:
    """封面标题。按真实跨度说「这些天 / 这几个月 / 这一年多」，
    不写死个月数——别人可能只用了两周，也可能用了两年。"""
    span = facts.get("span", {}) or {}
    try:
        days = int(span.get("total_days") or 0)
    except (TypeError, ValueError):
        days = 0
    months = max(1, round(days / 30))
    if days <= 45:
        head = "这些天"
    elif months <= 12:
        head = f"这 {months} 个月"
    else:
        head = "这一年多"
    return f"{head}的<br>一些记录"


def _daterange(facts) -> str:
    span = facts.get("span", {}) or {}
    first = str(span.get("first") or "")[:10].replace("-", ".")
    last = str(span.get("last") or "")[:10].replace("-", ".")
    return f"{first} — {last}" if first and last else ""


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
    parts = [f"{_SOURCE_NAMES.get(name, name)} 的 {n} 个会话"
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


def _unique(path: str) -> str:
    """路径已被占用时加 _2、_3。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"


def write_out(text: str, path: str, allow_overwrite: bool = True) -> str:
    """先写请求的路径；桌面不可写（沙箱）时回退到当前目录，并把真实路径返回。

    `allow_overwrite=False` 用于默认文件名：不覆盖任何已有文件，包括回退之后
    落在当前目录里的那一份。同一个文件名写两次是使用者最不希望看到的事。
    """
    if not allow_overwrite:
        path = _unique(path)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    except OSError as exc:
        fallback = os.path.abspath(os.path.basename(path))
        if not allow_overwrite:
            fallback = _unique(fallback)
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[warn] 无法写入 {path}（{exc.__class__.__name__}），已改写到 {fallback}")
        return fallback


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    MISSING.clear()   # 同一个进程里调第二次时，别把上一次的缺失也报出来

    out_path = None
    args = []
    i = 1
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
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
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不了 {facts_path}：{exc}")
        return 1

    try:
        with open(nar_path, encoding="utf-8") as fh:
            narrative = json.load(fh)
    except FileNotFoundError:
        print(f"找不到叙事文件：{nar_path}")
        print("照 narrative.example.json 的样子写一份。")
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不了 {nar_path}：{exc}")
        return 1

    if not isinstance(narrative, dict):
        print(f"{nar_path} 的顶层应该是一个对象，现在是 {type(narrative).__name__}。")
        return 1

    try:
        with open(tpl_path, encoding="utf-8") as fh:
            tpl = fh.read()
    except OSError as exc:
        print(f"读不了模板 {tpl_path}：{exc}")
        return 1

    if "cards" not in narrative and "essay" not in narrative:
        print("narrative.json 结构不对：既没有 cards 也没有 essay。")
        print("照 narrative.example.json 的样子写，至少要有其中一个。")
        return 1

    cards_src = narrative.get("cards") or []
    essay_src = narrative.get("essay") or []
    cards = "\n\n".join(card_html(c, facts) for c in cards_src)
    essay = "\n".join(f"<p>{subst(p, facts)}</p>" for p in essay_src)

    # 模板里那几句固定文案（标题、转场、页脚）默认是中文。报告的语种要跟数据一致，
    # 英文数据就由 narrative.chrome 覆盖掉；不写 chrome 就保持中文默认。
    chrome = narrative.get("chrome") or {}
    lang = chrome.get("lang") or "zh-CN"
    headline = chrome.get("headline") or _headline(facts)
    title = chrome.get("title") or headline.replace("<br>", " ")
    turn = chrome.get("turn") or "翻到这里，剩下的不是数字了"
    footer = chrome.get("footer") or (
        f"数据来自 {_source_line(facts)}，全部在本地读取。"
        "系统注入的上下文、指令与技能目录不计入。")

    page = (tpl.replace("<!-- LANG -->", lang)
               .replace("<!-- TITLE -->", title)
               .replace("<!-- HEADLINE -->", headline)
               .replace("<!-- RANGE -->", _daterange(facts))
               .replace("<!-- TURN -->", turn)
               .replace("<!-- FOOTER -->", footer)
               .replace("<!-- CARDS -->", cards)
               .replace("<!-- ESSAY -->", essay))
    if MISSING:
        print("[warn] 这些占位符在 facts.json 里找不到，已渲染为空："
              + "、".join(sorted(MISSING)))
    # 没能替换的占位符会原样留在报告里。MISSING 抓不到这种（根本没匹配上），
    # 所以渲染完再扫一遍正文，宁可多报一句也别让 {xxx} 出现在读者眼前。
    leftover = sorted(set(LEFTOVER.findall(
        "\n".join([cards, essay, headline, turn, footer]))))
    if leftover:
        print("[warn] 这些看着像占位符但没替换成功（检查花括号和拼写）："
              + "、".join(leftover[:8]) + ("…" if len(leftover) > 8 else ""))
    # 用户明确指定 --out 时可以覆盖；用默认文件名时一份都不覆盖
    final = write_out(page, out_path or default_out(),
                      allow_overwrite=bool(out_path))
    print(f"写入 {final}（{len(page):,} 字节，{len(cards_src)} 张卡）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
