# Agenttime

Claude Code / DeepSeek Harness / ZCode Skill —— 把你的 AI 对话历史变成一篇安静的个人散文。

## 这是什么

Agenttime 分析你和 AI 助手之间的所有对话记录，然后写一篇文章——关于你是怎样的人、在什么时间工作、如何与 AI 沟通、你的习惯和标准。

它不是效率报告。不是仪表盘。没有排行榜。就是一封信。

## 安装

将本目录放入你所用 Agent 的 skills 目录（目录名即 skill 名，此处为 `agenttime`）：

- Claude Code：`~/.claude/skills/`
- DeepSeek Harness：`~/.dsh/skills/`
- ZCode：`~/.zcode/skills/`

三处各放一份、互不干扰：`analyze.py` 按自身所在目录判定读哪一侧的会话库。

## 使用

在 Claude Code / DeepSeek Harness 中输入：

```
/agenttime
```

ZCode 里用 `$` 菜单选 skill，或直接说「回顾一下」。然后等一会儿。桌面上会出现一份 HTML 文件。打开看。

## 支持的数据源

脚本按自身所在目录自动选源（本侧优先，Claude Code 存档兜底）：

- Claude Code（`~/.claude/projects` 下的 JSONL 会话记录）
- DeepSeek Harness（`~/.dsh/sessions` 下的 `session[.vN].jsonl.zstd`）
- ZCode（`~/.zcode/cli/db/db.sqlite`）
- ChatGPT（将 `conversations.json` 放在桌面或下载文件夹中，自动检测）

**依赖**：除标准库外，只有读 DeepSeek Harness 会话需要 `zstandard`（`pip install zstandard`）。缺这个包不会报错，但 DSH 侧会静默降级去读 Claude Code 存档——`source_label` 显示成「Claude Code (N 次会话)」而你在 DSH 里跑，就是缺包。其余三个数据源纯标准库。

## 隐私

所有分析在你本地完成。数据从不离开你的电脑。

## 文件结构

```
agenttime/
├── SKILL.md       # Skill 定义（Agent 读取这个）
├── analyze.py     # 本地分析引擎（支持 CLI：python analyze.py）
├── template.html  # HTML 模板
└── README.md
```
