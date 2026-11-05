# Agenttime

Turn your local AI conversation history into a personal retrospective: a stream of cards, then an essay. Everything is read locally. No network, no upload.

**English** · [简体中文](README.zh-CN.md)

![Cover](docs/cover.png)

## What it is

Thirteen cards lay out the facts first — when you first spoke, how many times you sat down, how many sentences the busiest day held, how much you typed versus how much it thought. Then an essay covers what the cards cannot: the empty days, the things left unsaid, the half you never see.

It is not a productivity report or a dashboard. It is something you can leaf through.

![Card stream](docs/cards.png)

![Essay](docs/essay.png)

## Why

Spotify Wrapped and the annual reports Chinese apps ship every December all do the same thing: they give a person a shape for behaviour that was scattered across a year.

Agenttime does that for AI conversations. The extra bit of self-interest: it is written by the AI, in first person.

So the report contains lines like "I cut your messages wherever the gap exceeded ninety minutes" and "you never see any of these characters." The numbers are cold; the one writing them sits right beside them.

## Install

This repository *is* the skill. Put it in your agent's skills directory and you are done.

### DeepSeek Harness

```powershell
git clone https://github.com/hongshuxifan321/agenttime "$HOME\.dsh\skills\agenttime"
```

```bash
git clone https://github.com/hongshuxifan321/agenttime ~/.dsh/skills/agenttime
```

Restart the agent; it will pick the skill up.

### Any other agent, or several at once

```powershell
git clone https://github.com/hongshuxifan321/agenttime
cd agenttime
.\install.ps1
```

```bash
git clone https://github.com/hongshuxifan321/agenttime
cd agenttime
./install.sh
```

The script looks for skill directories that already exist on this machine — `~/.dsh/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.config/agents/skills` — and installs into every one it finds. If none exists, it uses `~/.dsh/skills`.

| Flag | Effect |
|---|---|
| `-Target <dir>` / `-t <dir>` | Install into a specific directory |
| `-Link` / `-l` | Symlink instead of copying, so edits apply immediately (on Windows this needs Developer Mode or an elevated shell; falls back to copying) |
| `-Force` / `-f` | Overwrite an existing install without asking |

### Let the agent do it

Hand the repository URL to your agent and ask it to clone the repo into its own skills directory. `SKILL.md` tells it what to do next.

Dependency:

```powershell
pip install zstandard
```

Only needed to read DeepSeek Harness logs. Statistics and rendering are pure standard library.

## Use

Invoke the skill from your agent, or just say "review my conversations".

Or run it by hand:

```powershell
python cli.py doctor                       # what can be read on this machine
python cli.py scan --out facts.json        # statistics
# write narrative.json (follow narrative.example.json)
python cli.py render facts.json narrative.json
```

Output defaults to `~/Desktop/agenttime_report_YYYYMMDD_HHMMSS.html`. The timestamp is down to the second, so running it several times a day — or once per agent — never overwrites an earlier report. If the desktop is not writable it falls back to the current directory and prints the real path, still without overwriting anything already there.

Passing `--out` does overwrite. That is a name you chose yourself.

Keep intermediates in `.agenttime/` under the current directory rather than `$env:TEMP`: inside the DSH sandbox the session TEMP is `Temp\dsh-xxxx` while an elevated process gets `Temp`, so files go missing halfway through.

## Three layers

| Layer | Who | Output |
|---|---|---|
| Statistics | `engine.py`, pure computation, no file access | `facts.json` |
| Narrative | the AI writes it — the only layer that needs judgement | `narrative.json` |
| Rendering | `render.py` + `template.html` | one HTML file |

`narrative.json` contains no digits, only placeholders like `{volume.msgs|comma}` resolved against `facts.json` at render time. Every number in the prose therefore matches the data; the AI cannot invent one.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | skill definition, what the AI reads |
| `cli.py` | command line: doctor / scan / render |
| `engine.py` | statistics over the unified session model |
| `adapters/` | one file per agent log format |
| `adapters/discover.py` | finds conversation stores that have no adapter yet |
| `render.py` | facts + narrative + template → single HTML |
| `template.html` | layout: cover / cards / divider / essay / footer |
| `narrative.example.json` | example narrative (fictional data, structure only) |
| `install.ps1` / `install.sh` | installers |

## Data sources: DSH first, discovery for the rest

Only **DeepSeek Harness** (`~/.dsh/sessions`) is officially supported today.

It is the only source verified against real data. Other agent log formats differ a lot: some mix a system prompt into user messages, some encrypt the reasoning, some record nothing locally at all. Shipping an adapter nobody has validated would only create false expectations.

But agenttime does not pretend other agents do not exist.

### Several agents at once

When more than one source is readable, `scan` **merges them into a single report**. Your conversations are spread across whatever agents you used; what you want is all of it, not one of them. `sources` in `facts.json` records how many sessions each one contributed.

For a single source: `python cli.py scan --source dsh`.

### What it cannot read, it says it cannot read

`doctor` looks in two places. First, fixed paths for known agents — Claude Code's `~/.claude/projects`, Codex's `~/.codex/sessions`, ZCode's `~/.zcode/cli/db`. These are nested several levels deep and the files are named by UUID, so a generic directory scan will never find them. Then a generic scan catches anything else whose name looks like a conversation store.

```
found 1 source:

  dsh  ~/.dsh/sessions
    40 sessions · 612 human messages
    filtered 481 injected system messages

these places hold conversation data that agenttime cannot read yet:

  Codex  ~/.codex/sessions
    98 candidate files, e.g. 2026/11/05/rollout-....jsonl.zst
  ZCode  ~/.zcode/cli/db/db.sqlite
    1 candidate file, e.g. db.sqlite
```

**On a machine without DSH this output is the whole point.** `doctor` exits non-zero, but not with a bare "not found" — it tells you what conversation data exists, where, and then admits it cannot read it. It will not assemble a report out of something else.

The generic scan is deliberately conservative: a directory or file name must contain `session` / `chat` / `conversation` / `history` / `thread`, and databases additionally need a corroborating directory name. Better to miss one than to treat an audit log as a conversation.

## Adding a source

Write one file under `adapters/` implementing two functions:

```python
def detect() -> str | None:
    """Return the data root, or None if absent."""
    ...

def load(root: str) -> tuple[list[Session], LoadReport]:
    """Read into the unified model. Human messages only; drop every injection."""
    ...
```

Then add a line to `REGISTRY` in `adapters/__init__.py`. Statistics and rendering stay untouched.

The hard part is always the injection filter in `load()`. DSH has a clean field, `source.kind == "user"`; most agents do not, so you have to infer from `isMeta`, `synthetic`, XML tags and similar markers. Get it wrong and the top-words card fills up with system prompt text — this is the easiest thing in the project to get wrong.

## Measurement notes

- Only human input counts. DSH writes runtime context, `AGENTS.md` and the skill catalog as `user/message` events; these are dropped by requiring `data.source.kind == "user"`. On the machine this was built on, 2,608 such events contained only 1,360 genuine messages — injected text outweighed real input 92×
- Timestamps come from human messages only, never from tool calls or title events
- "Thinking volume" is the character length of reasoning blocks, not the block count
- "A sitting" splits messages on gaps longer than 90 minutes, rather than using a session's first and last timestamp — sessions resume days later, and first/last arithmetic produces values like 453 hours
- Unreadable sessions and sessions with no human messages are reported in `quality`, never skipped silently

## Requirements

- Python 3.10+ (uses `str | None` syntax)
- `zstandard`, only for DSH logs
- Windows / macOS / Linux, paths expanded from `~`

## Privacy

Everything runs locally. No network, no upload. The report is a single HTML file and you decide where it goes. Read it before you show it to anyone — it is made of your conversations.

## License

MIT
