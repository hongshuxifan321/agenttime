# Agenttime

把本机的 AI 对话历史整理成一份**卡片流 + 散文**的个人回顾。数据全部本地处理，不联网、不上传。

## 这是什么

先 13 张卡片把三个月的事实摊开——第一句话是什么时候、一共坐下多少次、最密的一天说了多少句、你输入多少字而它想了多少字——最后用一篇散文写卡片讲不到的部分。

不是效率报告，不是仪表盘。是一份可以翻的东西。

## 安装

把这个目录放进 `~/.dsh/skills/agenttime`（目录名即 skill 名）。

依赖：`zstandard`（读 DSH 会话日志用）。

```powershell
pip install zstandard
```

## 使用

在 DeepSeek Harness 里输入 `/agenttime`，或说「回顾一下」。一会儿桌面上会多出一份 HTML。

## 文件结构

| 文件 | 作用 |
|---|---|
| `SKILL.md` | skill 定义，AI 读这个 |
| `cli.py` | 命令行入口：doctor / scan / render |
| `engine.py` | 统计层，吃统一会话结构，产出 `facts.json` |
| `adapters/` | 各 agent 的日志解析，每个 agent 一个文件 |
| `render.py` | 读 `facts.json` + `narrative.json` + 模板，输出单文件 HTML |
| `template.html` | 版式：封面 / 卡片流 / 转场 / 散文 / 页脚 |
| `narrative.example.json` | 叙事示例，AI 照着写每次的 `narrative.json` |

## 手动跑

```powershell
python cli.py doctor                 # 看看能读到哪些会话
python cli.py scan --out facts.json  # 统计
# 写 narrative.json（照 narrative.example.json 的结构）
python cli.py render facts.json narrative.json
```

默认输出 `~/Desktop/agenttime_report_YYYYMMDD.html`，桌面不可写时回退到当前目录并打印真实路径。

中间文件建议放当前目录的 `.agenttime/`，不用 `$env:TEMP`——沙箱会话的 TEMP 和提权进程的不是同一个目录（前者带 `dsh-xxxx` 后缀），中途提权会找不到文件。

## 支持的 agent

| agent | 日志位置 | 状态 |
|---|---|---|
| DeepSeek Harness | `~/.dsh/sessions` | 真机验证 |
| Claude Code | `~/.claude/projects` | fixture 验证（本机无真机数据） |
| ZCode | `~/.zcode/cli/db/db.sqlite` | 真机验证 |
| Codex | `~/.codex/sessions` | 能读文件，但**读不到真人输入**（见下） |

各源能提供的数据不一样，缺的就少做几张卡：

| | 对话 | 思考量 | 工具调用 | 审批 |
|---|---|---|---|---|
| DSH | ✓ | ✓ | ✓ | ✓ |
| Claude Code | ✓ | ✓ | ✓ | ✗ |
| ZCode | ✓ | ✓ | ✓ | ✗ |
| Codex | 常为空 | ✗ | ✗ | ✗ |

**关于 Codex**：它的 rollout 日志里，`response_item` 的 `role=user` 是发给模型的完整请求（混着 30K 字符的系统提示），而 `event_msg` 的 `UserMessage` 实测也全是系统提示。sqlite 里只有线程目录、没有消息内容。所以 adapter 保留着，但通常读不出东西——这比拿系统提示凑一份报告诚实。

Claude Code 的 adapter 按[公开的会话格式文档](https://github.com/neilberkman/ccrider/blob/v1.1.8/research/schema.md)写的，用 `tests/fixtures/` 里手工构造的会话回归测试。如果你有真实的 Claude Code 会话，跑一次 `python cli.py doctor` 就能确认读不读得到。

加一个新 agent = 在 `adapters/` 下写一个文件，实现 `detect()` 和 `load()`，然后在 `adapters/__init__.py` 的 `REGISTRY` 里加一行。统计和渲染都不用动。

## 环境要求

- Python 3.10+（用了 `str | None` 语法）
- `zstandard`：只有读 DSH 和 Codex 时需要，`pip install zstandard`
- Claude Code / ZCode 只用标准库
- macOS / Linux / Windows 都可以，路径用 `~` 展开

## 测试

```powershell
python tests/test_claude_code.py
```

本机没有 Claude Code，测试用 fixtures 里按官方 schema 构造的会话，覆盖：summary 标题、tool_result、isMeta、system-reminder、command 标签、子代理 agent-*.jsonl。改 adapter 后先跑这个。

默认输出 `~/Desktop/agenttime_report_YYYYMMDD.html`，桌面不可写时回退到当前目录并打印真实路径。

## 数据口径

只在本地读 `~/.dsh/sessions`。

DSH 会把运行时上下文、`AGENTS.md`、技能目录都写成 `user/message` 事件。这些不是用户说的话，必须按 `data.source.kind == "user"` 过滤掉——实测本机 2,608 条 `user/message` 里只有 1,360 条是真人输入，注入文本是真实输入的 92 倍。

其余口径：

- 时间戳只取真人消息，不混入工具调用和会话标题事件
- 「思考量」取 reasoning 块的文本长度，不是块数
- 「一次坐下」按消息间隔 90 分钟切分，不用会话首末时间
- 读不出来 / 没有真人消息的会话记在 `quality` 里，不静默跳过

## 隐私

所有分析在本机完成。报告是一份 HTML，写到哪里由你决定；给别人看之前自己过一眼。
