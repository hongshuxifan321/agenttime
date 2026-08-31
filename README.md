# Agenttime

Claude Code Skill —— 把你的 AI 对话历史变成一篇安静的个人散文。

## 这是什么

Agenttime 分析你和 Claude Code 之间的所有对话记录，然后写一篇文章——关于你是怎样的人、在什么时间工作、如何与 AI 沟通、你的习惯和标准。

它不是效率报告。不是仪表盘。没有排行榜。就是一封信。

## 安装

将本目录放入 `~/.zcode/skills/`（目录名即 skill 名，此处为 `agenttime`）。

## 使用

在 Claude Code 中输入：

```
/agenttime
```

然后等一会儿。桌面上会出现一份 HTML 文件。打开看。

## 支持的数据源

- Claude Code（自动读取本地 JSONL 会话记录）
- ChatGPT（将 `conversations.json` 放在桌面或下载文件夹中，自动检测）

## 隐私

所有分析在你本地完成。数据从不离开你的电脑。

## 文件结构

```
agenttime/
├── skill.md       # Skill 定义（Claude Code 读取这个）
├── analyze.py     # 本地分析引擎（支持 CLI：python analyze.py）
├── template.html  # HTML 模板
└── README.md
```
