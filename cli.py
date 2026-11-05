"""agenttime 命令行入口。

    python cli.py doctor                  看看这台机器上能读到哪些会话
    python cli.py scan --out facts.json   统计，产出 facts.json
    python cli.py render facts.json narrative.json [--out X.html]

也可以不带子命令直接跑 `python cli.py`，等价于 doctor。
"""

import argparse
import json
import os
import sys

# 让脚本从任意目录调用时都能 import 到同目录的包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine
from adapters import available, load


def _fmt(n: int) -> str:
    return f"{n:,}"


def cmd_doctor(_args) -> int:
    found = available()
    if not found:
        print("没有找到可读的会话记录。")
        print()
        print("agenttime 现在支持：")
        print("  DeepSeek Harness   ~/.dsh/sessions")
        print()
        print("如果你的 agent 不在列表里，说明它的日志格式还没适配。")
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

    print(f"默认使用 {found[0][0]}。运行 scan 开始统计。")
    return 0


def cmd_scan(args) -> int:
    found = available()
    if not found:
        print("没有找到可读的会话记录。先跑 `python cli.py doctor` 看看。")
        return 1

    if args.source == "auto":
        name, root = found[0]
    else:
        name, root = args.source, None
        if root is None and not any(n == name for n, _ in found):
            print(f"找不到数据源 {name}。先跑 `python cli.py doctor` 看看。")
            return 1

    sessions, report = load(name, root)
    if not sessions:
        print(f"{name} 的会话一条也没读出来。")
        print(f"目录：{report.root}")
        print(f"扫到 {report.dirs} 个会话目录，{report.read_errors} 个读取失败。")
        return 1

    facts = engine.build_facts(sessions, report)
    out = args.out or "facts.json"
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, ensure_ascii=False, indent=2)

    q = facts["quality"]
    print(f"数据源 {name} · {q['ok']} 个会话 · "
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
    p_scan.add_argument("--source", default="auto", help="数据源名，默认 auto")
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
