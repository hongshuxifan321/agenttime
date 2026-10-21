# Agenttime

DeepSeek Harness Skill —— 把你的 AI 对话历史变成一篇安静的个人散文。

## 这是什么

Agenttime 分析你和 AI 助手之间的所有对话记录，然后写一篇文章——关于你是怎样的人、在什么时间工作、如何与 AI 沟通、你的习惯和标准。

它不是效率报告。不是仪表盘。没有排行榜。就是一封信。

## 安装

把本目录放进 `~/.dsh/skills/`（目录名即 skill 名，此处为 `agenttime`）。

## 使用

在 DeepSeek Harness 里输入 `/agenttime`，或直接说「回顾一下」。然后等一会儿。桌面上会出现一份 HTML 文件，打开看。

## 数据源

- DeepSeek Harness 会话库（`~/.dsh/sessions` 下的 `session[.vN].jsonl.zstd`）
- ChatGPT 导出（把 `conversations.json` 放在桌面或下载文件夹，自动检测）

**依赖**：除标准库外，读 DSH 会话需要 `zstandard`（`pip install zstandard`）。ChatGPT 导出不依赖它。

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
