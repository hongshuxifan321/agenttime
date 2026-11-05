---
name: agenttime
description: 把本机 AI 对话历史整理成一份「卡片流 + 散文」的个人回顾。数据全部本地处理，不联网。触发词：对话回顾、会话回顾、个人回顾、AI 使用回顾、工作回顾、我的对话、回顾一下。用户主动调用时直接运行。
argument-hint: "无需参数，直接运行"
user-invocable: true
---

# Agenttime

把 `~/.dsh/sessions` 里的对话整理成一份单文件报告：先 13 张卡片摊开事实，再一篇散文写卡片讲不到的部分。默认落到桌面。

**两层分离**：`engine.py` 只算数据，`render.py` 只排版，你只写中间那层文字（`narrative.json`）。不要在文案里手写数字——一律用占位符。

## 执行流程

### 第一步：跑数据

中间文件放在**当前工作目录**下的 `.agenttime/`，不要用 `$env:TEMP`——沙箱会话的 TEMP 是 `Temp\dsh-xxxx`，提权进程的是 `Temp`，两边不一致，中途提权会找不到文件。

```powershell
$T = Join-Path (Get-Location) ".agenttime"
New-Item -ItemType Directory -Force -Path $T | Out-Null
python "$env:USERPROFILE\.dsh\skills\agenttime\cli.py" scan --out "$T\facts.json"
```

输出一份 `facts.json`。若报错（找不到数据源、没有时间戳），先跑 `cli.py doctor` 看这台机器上能读到什么，把结果告诉用户并停下。

`scan` 默认用 `doctor` 列出的第一个数据源。用户机器上有多个 agent 的历史时，问一句想用哪个，再带 `--source <名字>` 重跑。

### 第二步：写叙事

读 `facts.json`，照 `narrative.example.json` 的结构写一份 `narrative.json`（同样放在 `.agenttime/` 里）。

**这里是你唯一要动脑的地方**，规则见下方「写作要求」。

### 第三步：渲染

```powershell
python "$env:USERPROFILE\.dsh\skills\agenttime\cli.py" render "$T\facts.json" "$T\narrative.json"
```

默认写 `~/Desktop/agenttime_report_YYYYMMDD.html`。写不进去（沙箱）时会自动回退到当前目录并打印真实路径。

### 第四步：告知用户

只说一句「桌面上有份文件，自己看吧」之类。不要报字数、不要复述内容、不要提技术细节。

**例外**：如果 `render.py` 打印了回退警告（桌面不可写），要把真实路径告诉用户，并说明「桌面写不进去，文件在 XXX」。

## narrative.json 结构

```json
{
  "cards": [
    {
      "idx": "01",
      "kicker": "2026 年 8 月 14 日 — 11 月 5 日",
      "num": "{volume.sessions}",
      "unit": "场",
      "fact": "你和我之间，一共 <b>{volume.sessions}</b> 场对话，<b>{volume.msgs|comma}</b> 句话。",
      "aside": "平均每句 {volume.avg_chars} 个字。不长。"
    }
  ],
  "essay": [
    "8 月 14 日下午三点五十分，你打开我，说：「{span.first_text}」。",
    "我没有问为什么叫这个名字，你也没有说。"
  ]
}
```

三种卡片：

| 类型 | 必备字段 | 说明 |
|---|---|---|
| 数字卡 | `num` + `unit` | 大字 + 单位 |
| 列表卡 | `list_from` | 从 facts 取列表，目前只有 `"projects"` |
| 词云卡 | `chips_from` | 从 facts 取 `[[词, 次数], ...]`，取前 10 |

每张卡都必须有 `idx`、`kicker`；`fact` 和 `aside` 至少有一个。

## 占位符

文案里写 `{路径}`，渲染时从 `facts.json` 取值替换。支持三个过滤器：

- `{path|comma}` → 千分位：`16,183`
- `{path|round}` → 取整：`220`
- `{path|cnwan}` → 中文万数：`十一万`、`两千四百八十一万`

**不要手写数字**。能取就取，取不到再自己写。

## 可用路径

```
span.first            第一次对话，2026-08-14 15:50
span.first_time       15:50
span.first_text       第一句话原文
span.total_days       84
span.last             最后一次对话时间

volume.sessions       89        会话数
volume.msgs           1,355     真人消息数
volume.chars          112,869   真人输入总字符
volume.avg_chars      83.5      平均每句字数

rhythm.active_days    42        来过的天数
rhythm.max_streak     16        最长连续
rhythm.sittings       76        一次坐下的次数（间隔 90 分钟切分）
rhythm.avg_per_sitting 17.8     每次平均句数
rhythm.busiest_day    2026-08-16
rhythm.busiest_n      139       最密那天的句数
rhythm.busiest_titles [[标题, 句数], ...]   最密那天的会话
rhythm.peak_hour      10
rhythm.peak_count     151
rhythm.hour_dist      24 小时分布 {0..23: 次数}
rhythm.late_msgs      29        凌晨 0-5 点的消息数
rhythm.late_sessions  10
rhythm.latest         10 月 10 日 01:45

words.shortest        {len, text}    最短的一句
words.longest         {len, text}    最长的一句
words.thanks          0              「谢谢」出现次数
words.confirms        24
words.interrupts      38             打断词出现次数
words.en              [[英文词, 次数], ...]
words.cn              [[中文词, 次数], ...]

agent.tools_total     16,183
agent.tools_top       [[工具名, 次数], ...]
agent.tools_by_name   按名字取，如 agent.tools_by_name.pwsh
agent.reasoning_chars 24,819,142     思考总字符
agent.ratio           220            用户每 1 字对应多少思考字
agent.read_days       29             按每秒 10 字读完全部思考要几天
agent.approvals_asked 2,534
agent.approvals_granted 2,523
agent.denied          11

projects              [{title, n, start, end, tools}, ...]  最多 10 条
deepest_project       {title, n, start, end, tools}         工具调用最多的那条
quality               {dirs, ok, skipped_no_user, read_error, filtered_injected}
```

## 写作要求

### 卡片

- **每张卡只说一件事**。`kicker` 是一行小字（日期或语境），`fact` 是一句完整的话，`aside` 是余味
- **数字有分量**：`num` 是这张卡的主角，不要塞两个数字
- **13 张左右**。数据里没有的东西不要硬凑——没有凌晨活动就不做「深夜」卡，没有打断就不做「算了」卡。最少 8 张，最多 16 张
- `fact` 里可以用 `<b>...</b>` 强调关键数字

### 散文

- **40 段上下，1400-1600 字**。段落短，一句到两句一段，像说话，不像写文章
- **不要复述卡片里的数字**。卡片已经报过一遍，散文写卡片讲不到的：空白的日子、没说出口的部分、看不见的那一半
- **不要评价这个人**。写「那天你说了 139 句」，不写「你是个专注的人」。人格留给读者自己拼
- **可以有「我」**。整篇是 AI 的第一人称，这是这个 skill 和其他年度报告的区别
- 中文数字用 `|cnwan` 过滤器，阿拉伯数字直接写

### 语气

安静、克制、不煽情。像某个下午坐下来，把一本账翻完。

不要「你是最棒的」，不要「让我们一起」，不要「这一年你收获了」。

## 数据口径

- 只统计 `user/message` 且 `data.source.kind == "user"` 的消息。DSH 会把运行时上下文、`AGENTS.md`、技能目录都写成 `user/message`，这些必须剔除，否则词频全是系统文本
- 时间戳只取真人消息，不混入 tool/call、session/title
- 「思考量」是 reasoning 块的文本长度，不是块数
- 「一次坐下」按消息间隔 90 分钟切分，不用会话首末时间（会话可以隔几天恢复）
- 读不出来的会话记在 `quality` 里，不静默跳过

如果 `quality.filtered_injected` 是 0，说明这批数据没有注入，或者 DSH 改了格式——要在报告里说明。

## 注意事项

- 不要改 `engine.py` 和 `render.py`。分析和渲染是固定层，只有 `narrative.json` 是每次现写的
- 文章里不要出现 Markdown 标记
- 生成后不要自动打开浏览器
- 每次生成新文件，不覆盖之前的日期
