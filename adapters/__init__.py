"""数据源注册表。

加一个新 agent 只需要在这里多一行，engine 和 render 都不用动。
"""

from . import claude_code, codex, dsh, zcode

# (名字, 探测函数, 读取函数)
REGISTRY = [
    ("dsh", dsh.detect, dsh.load),
    ("claude_code", claude_code.detect, claude_code.load),
    ("codex", codex.detect, codex.load),
    ("zcode", zcode.detect, zcode.load),
]


def available() -> list[tuple[str, str]]:
    """返回 [(名字, 根目录)]，只包含真的找到数据的源。"""
    found = []
    for name, detect, _load in REGISTRY:
        try:
            root = detect()
        except Exception:
            root = None
        if root:
            found.append((name, root))
    return found


def load(name: str, root: str | None = None):
    for n, _detect, load_fn in REGISTRY:
        if n == name:
            return load_fn(root) if root else load_fn()
    raise ValueError(f"不认识的数据源：{name}（可用：{[n for n, _, _ in REGISTRY]}）")
