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

## 支持范围

**现在只官方支持 DeepSeek Harness**——它是唯一在真机上验证过的源（`~/.dsh/sessions`）。

其他 agent 的日志格式差异很大：有的把系统提示混进用户消息，有的把思考内容加密，有的根本不在本地留记录。没验证过的 adapter 发出来只会给人错误的期待，所以先只放一个。

但 agenttime 不会假装看不见别的 agent。`doctor` 会主动扫一遍你的主目录，把「看起来像对话数据、但还没有 adapter」的地方列出来：

```
找到 1 个数据源：

  dsh  ~/.dsh/sessions
    89 个会话 · 1,367 条真人消息
    挡掉系统注入 1,257 条

还有一些地方像对话数据，但还没有 adapter：

  ~/.raixis
    发现 3 个候选文件，例如 sessions/159ef894-....json
  ~/.codex
    发现 2 个候选文件，例如 session_index.jsonl
```

判断偏保守：目录名或文件名要带 `session` / `chat` / `conversation` / `history` / `thread` 才认，数据库还要目录名佐证。宁可漏报，也不要把审计日志当成对话。

## 加一个数据源

写一个文件，实现两个函数：

```python
# adapters/your_agent.py
def detect() -> str | None:
    """返回数据根目录；没有就返回 None。"""
    ...

def load(root: str) -> tuple[list[Session], LoadReport]:
    """读成统一结构。只放真人消息，系统注入全部挡掉。"""
    ...
```

然后在 `adapters/__init__.py` 的 `REGISTRY` 加一行。统计和渲染都不用动。

重点是 `load()` 里的注入过滤——这是整个项目最容易做错的地方。DSH 有 `source.kind == "user"` 这种干净字段，别的 agent 往往没有，得靠 `isMeta`、`synthetic`、XML 标签之类的特征自己判断。判断不干净的话，报告里的高频词会全是系统提示。

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
