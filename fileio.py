"""原子发布文本文件；不接触统计，不决定输出路径。"""

from __future__ import annotations

import os
import tempfile


def atomic_write_text(path: str, text: str, overwrite: bool = True) -> str:
    # 编码失败必须发生在创建文件前，尤其不能先截断旧文件。
    payload = text.encode("utf-8")
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".agenttime-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(temporary, path)
            return path
        base, ext = os.path.splitext(path)
        candidate, number = path, 2
        while True:
            try:
                # Windows rename 不替换已有目标，也不需要创建硬链接的权限。
                # POSIX rename 会替换目标，因此用原子的 link 占用名字。
                if os.name == "nt":
                    os.rename(temporary, candidate)
                else:
                    os.link(temporary, candidate)
                return candidate
            except FileExistsError:
                candidate = f"{base}_{number}{ext}"
                number += 1
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
