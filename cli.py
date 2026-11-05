"""agenttime 命令行入口。

    python cli.py doctor                  看看这台机器上能读到哪些会话
    python cli.py scan --out facts.json   统计，产出 facts.json
    python cli.py render facts.json narrative.json [--out X.html]

也可以不带子命令直接跑 `python cli.py`，等价于 doctor。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 让脚本从任意目录调用时都能 import 到同目录的包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine
from adapters import available, load
from adapters.base import LoadReport


def _fmt(n: int) -> str:
    return f"{n:,}"


def _top_dir(path: str) -> str:
    """把数据源路径折算到主目录下的第一层目录，用来排除已适配的源。"""
    home = os.path.expanduser("~")
    rel = os.path.relpath(path, home)
    return os.path.join(home, rel.split(os.sep)[0])


def _discover_unknown(adapted=()) -> list:
    """找出没有 adapter 的会话存储：先按已知 agent 路径探，再用通用扫描兜底。"""
    from adapters import discover
    exclude = [_top_dir(r) for r in adapted]
    items = []
    try:
        items = discover.known(exclude=exclude)
    except Exception:
        items = []
    try:
        # 已知路径已经点到的目录，通用扫描不再重复报
        generic = discover.scan(
            exclude=exclude + [_top_dir(i["path"]) for i in items])
    except Exception:
        generic = []
    for u in generic:
        items.append({"name": "", "path": u["path"],
                      "count": u["count"], "sample": u["sample"]})
    return items


def _print_unknown(items) -> None:
    for u in items:
        sample = u["sample"][0] if u.get("sample") else ""
        label = f"{u['name']}  " if u.get("name") else ""
        print(f"  {label}{u['path']}")
        print(f"    {_fmt(u['count'])} 个候选文件，例如 {sample}")
    print()


def _report_unknown(adapted=()) -> list:
    items = _discover_unknown(adapted)
    if not items:
        return []
    print("还有这些地方有对话数据，但 agenttime 还读不了：\n")
    _print_unknown(items)
    print("想让 agenttime 读它们，就在 adapters/ 下加一个文件（见 README）；")
    print("也可以把上面这段发出去，让别人来做。\n")
    return items


def _merge_reports(reports) -> LoadReport:
    m = LoadReport()
    m.source = "+".join(sorted({r.source for r in reports if r.source}))
    m.root = " · ".join(r.root for r in reports if r.root)
    for r in reports:
        m.dirs += r.dirs
        m.ok += r.ok
        m.skipped_no_user += r.skipped_no_user
        m.read_errors += r.read_errors
        m.filtered_injected += r.filtered_injected
    return m


def cmd_doctor(_args) -> int:
    found = available()
    if not found:
        print("agenttime 现在只官方支持 DeepSeek Harness（~/.dsh/sessions），"
              "这台机器上没有。")
        print()
        items = _discover_unknown()
        if items:
            print("不过这些地方有对话数据，只是 agenttime 还读不了：\n")
            _print_unknown(items)
            print("不会拿它们硬凑一份报告——读不了就是读不了。")
            print("想让 agenttime 读，就在 adapters/ 下加一个文件（见 README）；")
            print("也可以把上面这段发出去，让别人来做。\n")
        else:
            print("主目录里也没扫到别的对话数据。")
            print("如果你确实用过带本地日志的 agent，把它的数据目录告诉我，可以试着适配。\n")
        return 1

    print(f"找到 {len(found)} 个数据源：\n")
    for name, root in found:
        try:
            sessions, report = load(name, root)
        except Exception as exc:
            print(f"  {name}  {root}")
            print(f"    读取失败：{exc}\n")
            continue
        msgs = sum(len(s.msgs) for s in sessions)
        print(f"  {name}  {root}")
        print(f"    {report.ok} 个会话 · {_fmt(msgs)} 条真人消息")
        if report.filtered_injected:
            print(f"    挡掉系统注入 {_fmt(report.filtered_injected)} 条")
        if report.skipped_no_user:
            print(f"    跳过没有真人消息的会话 {report.skipped_no_user} 个")
        if report.read_errors:
            print(f"    读不出来 {report.read_errors} 个")
        print()

    if len(found) == 1:
        print(f"默认使用 {found[0][0]}。运行 scan 开始统计。\n")
    else:
        names = "、".join(n for n, _ in found)
        print(f"默认把 {names} 全部算进同一份报告。只想用其中一个：")
        print(f"  python cli.py scan --source {found[0][0]}\n")

    _report_unknown([r for _, r in found])
    return 0


def cmd_scan(args) -> int:
    found = available()
    if not found:
        print("没有找到可读的会话记录。先跑 `python cli.py doctor` 看它发现了什么。")
        return 1

    if args.source in ("all", "auto"):
        targets = found
    else:
        targets = [(n, r) for n, r in found if n == args.source]
        if not targets:
            names = "、".join(n for n, _ in found)
            print(f"找不到数据源 {args.source}。这台机器上有：{names}")
            return 1

    sessions, reports = [], []
    for name, root in targets:
        try:
            s, r = load(name, root)
        except Exception as exc:
            print(f"[warn] {name} 读取失败：{exc}")
            continue
        sessions.extend(s)
        reports.append(r)

    if not sessions:
        print(f"这些数据源一条会话都没读出来：" + "、".join(n for n, _ in targets))
        for r in reports:
            print(f"  {r.source}：{r.root}，扫到 {r.dirs} 个会话目录，"
                  f"{r.read_errors} 个读取失败")
        return 1

    report = _merge_reports(reports) if len(reports) > 1 else reports[0]
    facts = engine.build_facts(sessions, report)
    out = args.out or "facts.json"
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, ensure_ascii=False, indent=2)

    q = facts["quality"]
    print(f"数据源 {'、'.join(n for n, _ in targets)} · {q['ok']} 个会话 · "
          f"{_fmt(facts['volume']['msgs'])} 条消息 · "
          f"{_fmt(facts['volume']['chars'])} 个字符")
    if q["filtered_injected"]:
        print(f"挡掉系统注入 {_fmt(q['filtered_injected'])} 条")
    print(f"写入 {out}")
    return 0


def cmd_render(args) -> int:
    import render
    argv = ["render.py", args.facts, args.narrative]
    if args.out:
        argv += ["--out", args.out]
    return render.main(argv)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="agenttime", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="探测可读的会话记录")

    p_scan = sub.add_parser("scan", help="统计并产出 facts.json")
    p_scan.add_argument("--source", default="all",
                        help="数据源名；默认 all，把所有可用源合并成一份报告")
    p_scan.add_argument("--out", default="facts.json", help="输出路径")

    p_render = sub.add_parser("render", help="把 facts + narrative 渲染成 HTML")
    p_render.add_argument("facts", nargs="?", default="facts.json")
    p_render.add_argument("narrative", nargs="?", default="narrative.json")
    p_render.add_argument("--out", default=None, help="输出 HTML 路径")

    args = parser.parse_args(argv[1:])
    if args.cmd == "doctor" or not args.cmd:
        return cmd_doctor(args)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "render":
        return cmd_render(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
