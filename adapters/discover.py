"""主动发现：在不认识的数据源里找会话存储。

`doctor` 先用 REGISTRY 里的 adapter 探测已知格式，再用这里扫一遍用户主目录，
把「看起来像对话数据、但还没有 adapter」的地方列出来。

只做只读扫描，不打开文件内容，只在文件名/目录名层面判断，避免误碰隐私文件。

判断偏保守：宁可漏报，也不要把 Stata 的 audit 日志当成对话数据。
"""

from __future__ import annotations

import os

HOME = os.path.expanduser("~")

# 系统/开发工具目录，扫到也基本不是对话数据
_SKIP_DIRS = {
    ".ssh", ".gnupg", ".git", ".gitconfig", ".npm", ".nvm", ".cache", ".config",
    ".local", ".vscode", ".vscode-server", ".cursor-server", ".docker", ".gradle",
    ".m2", ".cargo", ".rustup", ".pyenv", ".conda", ".ipython", ".jupyter",
    ".matplotlib", ".anaconda", ".virtualenvs", ".bun", ".pnpm-store", ".yarn",
    ".android", ".azure", ".aws", ".kube", ".terraform.d", ".statamcp",
    ".dsh",                                          # 已适配，由 adapter 自己报
}

# 目录名带这些词，就把里面的 json/jsonl 全算作候选
_DIR_HINTS = ("session", "chat", "conversation", "history", "thread", "message", "transcript")
# 目录名没线索时，文件名本身要带这些词才算
_FILE_HINTS = ("session", "chat", "conversation", "history", "message", "thread")
_JSON_EXT = (".jsonl", ".jsonl.zst", ".jsonl.zstd", ".json")
_DB_EXT = (".sqlite", ".sqlite3", ".db")

_MIN_HITS = 2


def _is_json(name: str) -> bool:
    return name.lower().endswith(_JSON_EXT)


def _is_db(name: str) -> bool:
    return name.lower().endswith(_DB_EXT)


def _dir_hint(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _DIR_HINTS)


def _file_hint(name: str) -> bool:
    low = name.lower()
    if low == "conversations.json":
        return True
    return any(h in low for h in _FILE_HINTS)


def _scan_depth2(root: str, max_entries: int = 5000) -> list[str]:
    hits: list[str] = []
    seen = 0
    try:
        level1 = list(os.scandir(root))
    except OSError:
        return hits

    for e1 in level1:
        seen += 1
        if seen > max_entries:
            break

        if not e1.is_dir(follow_symlinks=False):
            if _is_json(e1.name) and _file_hint(e1.name):
                hits.append(e1.name)
            continue

        dir_is_hint = _dir_hint(e1.name)
        try:
            children = list(os.scandir(e1.path))
        except OSError:
            continue

        for e2 in children:
            seen += 1
            if seen > max_entries:
                return hits
            if not e2.is_file():
                continue
            name = e2.name
            if _is_db(name):
                # 数据库要靠目录名佐证，不然会把各种缓存 db 算进来
                if dir_is_hint or _dir_hint(os.path.dirname(e1.path)):
                    hits.append(os.path.join(e1.name, name))
            elif _is_json(name) and (_dir_hint(e1.name) or _file_hint(name)):
                hits.append(os.path.join(e1.name, name))
    return hits


def scan(home: str = HOME, exclude=()) -> list[dict]:
    """返回 [{path, count, sample}]，按命中数排序。

    `exclude` 是已经适配好的顶层目录（绝对路径），扫到就跳过。
    """
    found = []
    exclude = {os.path.abspath(p) for p in exclude}
    try:
        entries = list(os.scandir(home))
    except OSError:
        return found

    for e in entries:
        if not e.is_dir(follow_symlinks=False):
            continue
        name = e.name
        if not name.startswith(".") or name in _SKIP_DIRS:
            continue
        if os.path.abspath(e.path) in exclude:
            continue
        hits = _scan_depth2(e.path)
        if len(hits) >= _MIN_HITS:
            found.append({"path": e.path, "count": len(hits), "sample": hits[:3]})

    found.sort(key=lambda x: -x["count"])
    return found


# ── 已知 agent 的固定路径 ─────────────────────────────────────────────
# 通用扫描只到 depth 2，够不着 .claude/projects/<项目>/<uuid>.jsonl 这种结构，
# 而且文件名是 uuid，不带任何关键词。对已知 agent 直接按固定路径探。
# 这里只列确认过的位置——猜错会给人假的希望。
_KNOWN = (
    (".dsh/sessions", "DeepSeek Harness"),
    (".claude/projects", "Claude Code"),
    (".codex/sessions", "Codex"),
    (".zcode/cli/db/db.sqlite", "ZCode"),
    (".raixis/sessions", "raixis"),
)

_MAX_LIST = 4000


def _top(path: str, home: str) -> str:
    rel = os.path.relpath(path, home)
    return os.path.join(home, rel.split(os.sep)[0])


def _walk_files(path: str) -> list[str]:
    """列出候选文件（相对路径）。文件本身就算一个，目录则递归找。"""
    if os.path.isfile(path):
        return [os.path.basename(path)]
    out = []
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            if _is_json(fn) or _is_db(fn):
                out.append(os.path.relpath(os.path.join(dirpath, fn), path))
                if len(out) >= _MAX_LIST:
                    return out
    return out


def known(home: str = HOME, exclude=()) -> list[dict]:
    """按已知 agent 的固定路径探测，比通用扫描可靠。

    `exclude` 是已适配好的顶层目录（绝对路径），命中就跳过。
    返回 [{name, path, count, sample}]。
    """
    exclude_top = {os.path.abspath(p) for p in exclude}
    out = []
    for rel, name in _KNOWN:
        path = os.path.join(home, *rel.split("/"))
        if not os.path.exists(path):
            continue
        if os.path.abspath(_top(path, home)) in exclude_top:
            continue
        files = _walk_files(path)
        if not files:
            continue
        out.append({"name": name, "path": path, "count": len(files),
                    "sample": files[:3]})
    out.sort(key=lambda x: -x["count"])
    return out
