"""数据源注册表。

现在只官方支持 DeepSeek Harness。其他 agent 的日志格式差异太大，
没有在真机上验证过的 adapter 不如不放——`doctor` 的发现层会告诉你
机器上还有哪些地方可能有对话数据，要不要适配由你决定。

加一个新 agent：
1. 在 adapters/ 下新建一个文件，实现 detect() 和 load()
2. 在这里的 REGISTRY 加一行
统计和渲染都不用动。
"""

from . import dsh

# (名字, 探测函数, 读取函数)
REGISTRY = [
    ("dsh", dsh.detect, dsh.load),
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
    raise ValueError(
        f"不认识的数据源：{name}（可用：{[n for n, _, _ in REGISTRY]}）")
