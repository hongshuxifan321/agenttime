---
name: agenttime
description: 把本机 AI 对话历史整理成一份「卡片流 + 散文」的个人回顾。数据全部本地处理，不联网。触发词：对话回顾、会话回顾、个人回顾、AI 使用回顾、工作回顾、我的对话、回顾一下。用户主动调用时直接运行。
argument-hint: "无需参数，直接运行"
user-invocable: true
---

# Agenttime

把 `~/.dsh/sessions` 里的对话整理成一份单文件报告：先 13 张卡片摊开事实，再一篇散文写卡片讲不到的部分。默认落到桌面。

**三层分离**：`engine.py` 只算数据，`render.py` 只排版，你只写中间那层文字（`narrative.json`）。不要在文案里手写数字——一律用占位符。

## 执行流程

### 第一步：跑数据

先确定 skill 目录。**不要假设它装在 `~/.dsh/skills/`**——用户可能把它放在 `~/.claude/skills/` 或别处。运行环境一般会告诉（DSH 在 skill 内容里给 `Base directory`）。

拿不到路径就按常见位置挨个试，看哪个里面有 `cli.py`：`~/.dsh/skills/agenttime`、`~/.claude/skills/agenttime`、`~/.codex/skills/agenttime`。

```powershell
$SKILL = "<本 SKILL.md 所在目录>"
$T = Join-Path (Get-Location) ".agenttime"
New-Item -ItemType Directory -Force -Path $T | Out-Null
python "$SKILL\cli.py" scan --out "$T\facts.json"
```

非 Windows 平台换成 bash：`mkdir -p .agenttime && python "$SKILL/cli.py" scan --out .agenttime/facts.json`。

中间文件放**当前工作目录**下的 `.agenttime/`，不要用 `$env:TEMP`：在 DSH 的沙箱里，会话 TEMP 是 `Temp\dsh-xxxx`，提权进程的是 `Temp`，中途提权会找不到文件。

如果提示缺少 `zstandard`，用运行 CLI 的同一个 Python 执行 `python -m pip install zstandard`，再重跑；这是依赖未安装，不代表日志缺少时间戳。

输出一份 `facts.json`。若报错（找不到数据源、没有时间戳），先跑 `cli.py doctor` 看这台机器上能读到什么，把结果告诉用户并停下。

`scan` 默认把所有可读的数据源**合并成一份报告**。用户的对话散在几个 agent 里，他要的是「我的全部」，不是其中一个——所以不要问「你想用哪个」。只有用户自己说「只看 Claude 的」时，才带 `--source <名字>` 重跑。

只有一个数据源时（现在是大多数情况），这条不用管。

### 第二步：写叙事

读 `facts.json`，照 `narrative.example.json` 的结构写一份 `narrative.json`（同样放在 `.agenttime/` 里）。

**`narrative.example.json` 里的叙述是虚构的**。它只示范结构和语气，内容一律要换成这台机器上的真实数据。照着抄会得到一份不属于这个人的报告。日期和数量同样通过占位符取值，例如 `{span.first_date}`；写死的统计数字会被拦截。

**这里是你唯一要动脑的地方**，规则见下方「写作要求」。

### 第三步：渲染

```powershell
python "$SKILL\cli.py" render "$T\facts.json" "$T\narrative.json"
```

默认写 `~/Desktop/agenttime_report_YYYYMMDD_HHMMSS.html`。文件名带时分秒，同一天跑几次、几个 agent 各出一份都不会互相覆盖——**不要自己改成固定的名字**，那样第二份会把第一份盖掉。

默认输出写不进去（沙箱）时会回退到当前目录并打印真实路径；回退之后同样不覆盖已有文件。显式指定 `--out` 时，写入失败就停止，不改写到其他路径。报告和 facts 都先写临时文件，成功后再原子落盘；失败时保留旧文件。

### 第四步：告知用户

只说一句「桌面上有份文件，自己看吧」之类。不要报字数、不要复述内容、不要提技术细节。

**例外**：如果 `render.py` 打印了回退警告（桌面不可写），要把真实路径告诉用户，并说明「桌面写不进去，文件在 XXX」。

## narrative.json 结构

```json
{
  "cards": [
    {
      "idx": "01",
      "kicker": "{span.first_day} — {span.last_day}",
      "num": "{volume.sessions}",
      "unit": "场",
      "fact": "你和我之间，一共 <b>{volume.sessions}</b> 场对话，<b>{volume.msgs|comma}</b> 句话。",
      "aside": "平均每句 {volume.avg_chars} 个字。不长。"
    }
  ],
  "essay": [
    "第一天，你打开我，说：「{span.first_text}」。",
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

每张卡都必须有 `idx`、`kicker`；`fact` 和 `aside` 至少有一个非空。卡片编号不得重复。缺字段、项目列表坏行、错误类型、非法数值和不认识的占位符过滤器都会停止渲染，不会补值或静默跳过。

### 报告的语言

模板里那几句固定文案默认是中文：封面标题、转场句、页脚。报告语种要跟数据一致——用户拿英文跟我对话，就该拿到英文报告，不能只有内文是英文、外壳还是中文。

在 `narrative.json` 里加 `chrome` 覆盖，四个字段都可选，不写就用中文默认：

```json
"chrome": {
  "title": "{span.months} months, in numbers",
  "headline": "{span.months} months<br>in numbers",
  "turn": "Past this point, it stops being numbers",
  "footer": "Built from DeepSeek Harness. Everything read locally."
}
```

`headline` 可以带 `<br>`，`title` 会被拿去当浏览器标签页标题，别放标签。卡片和散文是你自己写的，语种自然跟着走。

## 占位符

文案里写 `{路径}`，渲染时从 `facts.json` 取值替换。支持三个过滤器：

- `{path|comma}` → 千分位，只加分隔符不改数值：`16,794`、`82.5`、`1,234.5`
- `{path|round}` → 四舍五入取整（不是银行家舍入）：`82.5` → `83`
- `{path|cnwan}` → 中文万数：`四万`、`四百四十七万`（`cn` 是别名）

组合规则：`round` 可以和另外两个任一组合，先取整再格式化（`{x|round|cnwan}` → `八十三`，`{x|round|comma}` → `1,234,568`）。`comma` 和 `cnwan` 是两种输出格式，同时用会报错；`cnwan` 只接受整数，小数不加 `|round` 会报错；数字过滤器用在文字上也会报错。报错会写出路径、用到的过滤器和原因，不会静默忽略其中任何一个。

**不要手写统计数字**，中文数量也要用占位符。取不到就删去对应断言，或先在统计层补充有依据的事实；渲染层不计算、不推断、不补零。`idx` 是卡片的版式编号，不属于统计值。

`num` 必须是单个占位符。正文和 chrome 会检查手写阿拉伯数字及常见中文数量表达；普通叙述中的「一些」「第一句话」可以保留。文字是否作出无依据的数量断言，仍需写作时核对。

正文只保留无属性的 `<b>` 标签，封面 headline 可使用 `<br>`；其余 HTML 转义成文字，不会执行脚本或加载远程资源。chrome 的 title、headline、turn、footer 都支持占位符。

### 有些值是空的

数据里没有的东西，`facts.json` 里就是 `null` 或空列表——`rhythm.latest`（没有凌晨消息时）、`agent.tools_top`（数据源没记工具调用时，整个列表是空的，`{agent.tools_top.0.0}` 也就取不到）、`deepest_project`（同样）、`words.longest` 之类。引用这些空值会使渲染失败，不生成报告。

写之前先在 `facts.json` 里看一眼。为空就不要写那句话，也不要用那个占位符。`render.py` 会打印 `[error]` 并停止，看到缺失路径就修改叙事或重新生成 facts。旧 facts 缺少新字段时要重跑 scan，不让渲染层补算。

同理，卡片本身也要跟着数据走：没有凌晨活动就不做「深夜」卡，没有打断就不做「算了」卡。

### 有些原话只是片段

`words.longest.len` 是完整消息的字符数，`words.longest.text` 最多保留开头 60 个字符。`words.longest.truncated` 表示读取消息时原文是否已被截断；为 `false` 或缺失，也不能据此认定 `text` 是全文。引用时写「最长那句的开头是……」或明确标作节选，除非已核对 `text` 覆盖全文；不要根据片段补写后文。

## 可用路径

```
span.first            第一次对话，2026-03-02 15:29
span.first_date       2026年03月02日
span.first_time       15:29
span.first_hour       15
span.first_text       第一句话原文
span.last             最后一次对话时间
span.last_text        最后一句话原文
span.total_days       107
span.months           4         按 total_days / 30 四舍五入，最少 1；在统计层计算
span.first_day        2026.03.02
span.last_day         2026.06.16

volume.sessions       40        会话数
volume.msgs           612       真人消息数
volume.chars          43,661    真人输入总字符
volume.avg_chars      71.3      平均每句字数

rhythm.active_days    43        来过的天数
rhythm.max_streak     6         最长连续
rhythm.sittings       92        一次坐下的次数（间隔 90 分钟切分）
rhythm.avg_per_sitting 6.7      每次平均句数
rhythm.busiest_day    2026-05-09
rhythm.busiest_n      59        最密那天的句数
rhythm.busiest_titles [[标题, 句数], ...]   最密那天的会话
rhythm.peak_hour      18
rhythm.peak_count     65
rhythm.hour_dist      24 小时分布 {0..23: 次数}
rhythm.late_msgs      57        凌晨 0-5 点的消息数
rhythm.late_sessions  9
rhythm.latest         06 月 05 日 00:03

words.shortest        {len, text}    最短的一句
words.longest         {len, text, truncated}    最长的一句；len 为全文字符数，text 最多取开头 60 字符
words.thanks          1              「谢谢」出现次数
words.confirms        1
words.interrupts      117            打断词出现次数
words.en              [[英文词, 次数], ...]
words.cn              [[中文词, 次数], ...]

agent.tools_total     16,794
agent.tools_top       [[工具名, 次数], ...]
agent.tools_by_name   按名字取，如 agent.tools_by_name.write
agent.reasoning_chars 4,476,447      思考总字符
agent.ratio           102.5          用户每 1 字对应多少思考字
agent.read_chars_per_second 10   阅读速度假设
agent.read_days       5              按每秒 10 字读完全部思考要几天
agent.approvals_asked 714
agent.approvals_granted 698
agent.denied          14        明确 rejected 的决定数
agent.cancelled       1         明确 cancelled 的决定数
agent.pending         1         尚无决定的询问数
agent.unknown         0         其他无法识别的决定结果

projects              [{title, n, start, end, tools}, ...]  最多 10 条
                      按消息数排。工具调用最多的那条如果挤不进前 10，会补在末尾，
                      保证 deepest_project 一定能在列表里找到
deepest_project       {title, n, start, end, tools}         工具调用最多的那条
sources               {dsh: 40}        每个数据源贡献多少会话
quality               {dirs, ok, skipped_no_user, read_error,
                       filtered_injected, malformed_events, error_hint}
```

列表和字典按下标取，用点号：`{rhythm.busiest_titles.0.0}` 是「最密那天的第一个会话标题」，`{agent.tools_top.0.0}` 是「用得最多的那个工具名」，`{agent.tools_top.0.1}` 是它的次数。取不到（下标越界 / 列表为空）就渲染成空字符串，同时使渲染返回非 0，不写报告。

## 写作要求

### 卡片

- **每张卡只说一件事**。`kicker` 是一行小字（日期或语境），`fact` 是一句完整的话，`aside` 是余味
- **数字有分量**：`num` 是这张卡的主角，不要塞两个数字
- **13 张左右**。数据里没有的东西不要硬凑——没有凌晨活动就不做「深夜」卡，没有打断就不做「算了」卡。最少 8 张，最多 16 张
- `fact` 里可以用 `<b>...</b>` 强调关键数字

### 散文

- **40 段上下，1400-1600 字**。段落短，一句到两句一段，像说话，不像写文章
- **不要复述卡片里的数字**。卡片已经报过一遍，散文写卡片讲不到的：空白的日子、没说出口的部分、看不见的那一半
- **事实要有出处，未知就留白**。只把 facts 中能核对的内容写成事实。没有消息不代表没有工作，也不能据此编造空白日子里的经历、情绪或动机；标题、词频和消息长度同样不能证明这些。可以写「我不知道」，不必为凑篇幅安排情节。示例里的情节不作为事实依据。
- **不要评价这个人**。写「那天你说了 139 句」，不写「你是个专注的人」。人格留给读者自己拼
- **可以有「我」**。整篇是 AI 的第一人称，这是这个 skill 和其他年度报告的区别
- 中文统计数字用 `|cnwan` 过滤器，阿拉伯统计数字也用占位符

### 语气

安静、克制、不煽情。像某个下午坐下来，把一本账翻完。

不要「你是最棒的」，不要「让我们一起」，不要「这一年你收获了」。

## 数据源

现在只官方支持 DeepSeek Harness（`~/.dsh/sessions`）。

`doctor` 会额外扫一遍主目录，把「像对话数据、但还没有 adapter」的地方列出来。看到这种输出时，**如实告诉用户**：这里可能有对话，但 agenttime 还读不了，需要先写 adapter。不要换源硬凑，也不要拿别的数据代替。

### 用户机器上没有 DSH

这是最常见的失败路径：看到这个 skill 的人多半在用别的 agent。`doctor` 这时返回非 0，并列出它找到的其它对话数据（Claude Code、Codex、ZCode 等，含路径和文件数）。

这时候：

- **不要报错就结束**，也不要自己想个办法凑数据
- 把 doctor 的输出**原样**告诉用户：他机器上有什么、在哪个目录、多少个文件
- 说清楚 agenttime 现在读不了。「读不了」就是读不了，不要模糊成「可能可以」
- 如果用户想让它读，指路 README 的「加一个数据源」；真正的难点是过滤系统注入，不是解析格式

只有当用户明确要求「你帮我写一个 adapter」时才动手，写完在真机数据上验证过再交付。

DSH 能提供对话、思考量、工具调用、审批次数这四类；以后适配别的 agent 时，缺哪类就少做对应的卡片——没有审批就不做「行不行」，没有工具就不做「我替你敲了多少次键」。

## 数据口径

- 有 source 时只统计 `user/message` 且 `data.source.kind == "user"` 的消息。没有 source 的旧格式保留；source 存在但类型错误或缺少 kind 时记为坏事件，不当作真人消息。DSH 会把运行时上下文、`AGENTS.md`、技能目录都写成 `user/message`，这些必须剔除，否则词频全是系统文本
- 时间戳只取真人消息，不混入 tool/call、session/title
- 「思考量」是 reasoning 块的文本长度，不是块数
- 「一次坐下」按消息间隔 90 分钟切分，不用会话首末时间（会话可以隔几天恢复）
- 读不出来的会话和语法损坏的 JSON 行记在 `quality` 里，不静默跳过
- 「最晚的一次」按凌晨消息的本地钟点比较，不按日期新旧
- 审批拒绝只数明确 rejected；取消、未决、未知结果分别统计，不用询问数减同意数代替拒绝数

如果 `quality.filtered_injected` 是 0，说明这批数据没有注入，或者 DSH 改了格式——要在报告里说明。

## 注意事项

- 不要改 `engine.py` 和 `render.py`。分析和渲染是固定层，只有 `narrative.json` 是每次现写的
- 文章里不要出现 Markdown 标记
- 生成后不要自动打开浏览器
- 每次生成新文件，不覆盖之前生成的任何报告
- 动过 `engine.py` / `render.py` / `fileio.py` / `validation.py` / `adapters/` 之后先跑 `python tests/test_agenttime.py`（它会把 `tests/test_boundaries.py` 和安装器测试一并带上）
