"""Claude Code adapter 的回归测试。

本机没有 Claude Code，用 fixtures 里按官方 schema 手构造的会话跑。
改动 adapter 后先跑这个，确认过滤规则没坏。

    python tests/test_claude_code.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from adapters import claude_code  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "claude_code")


def main() -> int:
    sessions, report = claude_code.load(FIXTURE)

    failures = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"（期望 {want!r}）"))
        if not ok:
            failures.append(name)

    print("会话：")
    check("会话数（子代理不算）", len(sessions), 1)
    s = sessions[0] if sessions else None
    check("标题取自 summary", s.title if s else None, "修一个滑块组件的样式问题")
    check("source", s.source if s else None, "claude_code")

    print("\n注入过滤：")
    texts = [m.text for m in s.msgs] if s else []
    check("真人消息数", len(texts), 3)
    check("第一条", texts[0] if texts else None, "帮我把这个滑块改窄一点")
    check("system-reminder 被剥掉", texts[1] if len(texts) > 1 else None, "改好了吗")
    check("command 标签被剥掉", texts[2] if len(texts) > 2 else None, "顺便把轨道的颜色也调一下")
    check("tool_result 不算真人消息", any("slider" in t for t in texts), False)
    check("纯注入条目被丢掉", any("只有注入" in t for t in texts), False)

    print("\n统计：")
    check("工具", dict(s.tools) if s else None, {"Read": 1})
    think = s.reasoning_chars if s else 0
    check("思考字符 > 0", think > 50, True)
    check("时间戳排序", texts == [m.text for m in s.msgs], True)

    print("\n读取记账：")
    check("扫到会话文件（不含 agent-*）", report.dirs, 1)
    check("成功", report.ok, 1)
    check("挡掉的注入条数", report.filtered_injected, 3)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "、".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
