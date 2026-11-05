"""facts.json + narrative.json → 单文件 HTML

叙事和渲染分离：narrative.json 只放文字，数字用 {facts 路径} 占位，
渲染时从 facts.json 取值并格式化。这样文案里的数字永远和数据一致。

用法：
    python render.py                      # 默认 facts.json / narrative.json / template.html
    python render.py --out D:\\x.html      # 指定输出
"""

import html
import json
import os
import re
import sys
from datetime import date

PLACE = re.compile(r"\{([A-Za-z_][\w.\-]*)((?:\|[a-z]+)*)\}")


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


def cn_num(n: int) -> str:
    """整数转中文，散文中「十一万」「两千四百八十一万」这类读感用。"""
    n = int(n)
    if n < 10:
        return _CN_D[n]
    if n < 20:
        return "十" + (_CN_D[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_D[n // 10] + "十" + (_CN_D[n % 10] if n % 10 else "")
    if n < 1000:
        s = _CN_D[n // 100] + "百"
        r = n % 100
        if r == 0:
            return s
        return s + ("零" + _CN_D[r] if r < 10 else cn_num(r))
    if n < 10000:
        s = ("两千" if n // 1000 == 2 else _CN_D[n // 1000] + "千")
        r = n % 1000
        if r == 0:
            return s
        return s + ("零" + cn_num(r) if r < 100 else cn_num(r))
    s = cn_num(n // 10000) + "万"
    r = n % 10000
    if r == 0:
        return s
    return s + ("零" + cn_num(r) if r < 1000 else cn_num(r))


def _fmt(val, filters):
    if isinstance(val, bool) or val is None:
        s = "" if val is None else str(val).lower()
    elif isinstance(val, int):
        if "cnwan" in filters:
            s = cn_num(val // 10000) + "万" if val >= 10000 else cn_num(val)
        elif "cn" in filters:
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
        if val == "" or val is None:
            MISSING.add(path)
        return _fmt(val, (m.group(2) or "").split("|"))
    return PLACE.sub(repl, tpl)


def _num_block(num, unit):
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


def default_out() -> str:
    name = f"agenttime_report_{date.today().strftime('%Y%m%d')}.html"
    return os.path.join(os.path.expanduser("~"), "Desktop", name)


def write_out(text: str, path: str) -> str:
    """先写请求的路径；桌面不可写（沙箱）时回退到当前目录，并把真实路径返回。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    except OSError as exc:
        fallback = os.path.abspath(os.path.basename(path))
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[warn] 无法写入 {path}（{exc.__class__.__name__}），已改写到 {fallback}")
        return fallback


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

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

    with open(facts_path, encoding="utf-8") as fh:
        facts = json.load(fh)
    with open(nar_path, encoding="utf-8") as fh:
        narrative = json.load(fh)
    with open(tpl_path, encoding="utf-8") as fh:
        tpl = fh.read()

    cards = "\n\n".join(card_html(c, facts) for c in narrative["cards"])
    essay = "\n".join(f"<p>{subst(p, facts)}</p>" for p in narrative["essay"])

    page = tpl.replace("<!-- CARDS -->", cards).replace("<!-- ESSAY -->", essay)
    if MISSING:
        print("[warn] 这些占位符在 facts.json 里找不到，已渲染为空："
              + "、".join(sorted(MISSING)))
    final = write_out(page, out_path or default_out())
    print(f"写入 {final}（{len(page):,} 字节，{len(narrative['cards'])} 张卡）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
