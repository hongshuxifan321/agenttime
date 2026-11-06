"""校验 facts 中已提供的字段；不计算、修补或生成任何统计值。"""

from __future__ import annotations

import math
import re


def check_values(data):
    """JSON 可能包含孤立代理字符、NaN/Infinity；也检查未知扩展字段。"""
    stack = [("root", data)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError:
                return f"{path} 含有无法编码的 Unicode 字符"
        elif isinstance(value, float) and not math.isfinite(value):
            return f"{path} 必须是有限数值"
        elif isinstance(value, dict):
            for key, item in value.items():
                try:
                    str(key).encode("utf-8")
                except UnicodeError:
                    return f"{path} 的字段名含有无法编码的 Unicode 字符"
                stack.append((path + "." + str(key), item))
        elif isinstance(value, (list, tuple)):
            stack.extend((f"{path}.{i}", item) for i, item in enumerate(value))
    return ""


def _object(value, path, integers=(), numbers=(), strings=()):
    if not isinstance(value, dict):
        return f"{path} 应该是对象"
    for key in integers:
        if key in value and (type(value[key]) is not int or value[key] < 0):
            return f"{path}.{key} 应该是非负整数（不能是 bool）"
    for key in numbers:
        if key in value and (type(value[key]) not in (int, float) or value[key] < 0):
            return f"{path}.{key} 应该是非负有限数值（不能是 bool）"
    for key in strings:
        if key in value and not isinstance(value[key], str):
            return f"{path}.{key} 应该是字符串"
    return ""


def _counts(value, path):
    if not isinstance(value, dict):
        return f"{path} 应该是对象"
    for key, count in value.items():
        if type(count) is not int or count < 0:
            return f"{path}.{key} 应该是非负整数（不能是 bool）"
    return ""


def _pairs(value, path):
    if not isinstance(value, list):
        return f"{path} 应该是数组"
    for i, item in enumerate(value):
        if (not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or type(item[1]) is not int or item[1] < 0):
            return f"{path}.{i} 应该是 [文字, 非负整数]"
    return ""


def _project(value, path):
    error = _object(value, path, integers=("n", "tools"), strings=("title", "start", "end"))
    if error:
        return error
    for key in ("title", "start", "n"):
        if key not in value:
            return f"{path} 缺少 {key}"
    return ""


def check_facts(facts):
    if not isinstance(facts, dict):
        return "facts 顶层应该是对象"
    error = check_values(facts)
    if error:
        return error
    schema = {
        "span": (("first_hour", "total_days", "months"), (),
                 ("first", "first_time", "last", "first_date", "first_text", "last_text", "first_day", "last_day")),
        "volume": (("sessions", "msgs", "chars"), ("avg_chars",), ()),
        "rhythm": (("active_days", "max_streak", "sittings", "busiest_n", "peak_hour", "peak_count", "late_msgs", "late_sessions"),
                   ("avg_per_sitting",), ("busiest_day",)),
        "words": (("thanks", "confirms", "interrupts"), (), ()),
        "agent": (("tools_total", "reasoning_chars", "read_days", "read_chars_per_second", "approvals_asked", "approvals_granted",
                   "denied", "cancelled", "pending", "unknown"), ("ratio",), ()),
        "quality": (("dirs", "ok", "skipped_no_user", "read_error", "filtered_injected", "malformed_events"), (), ("error_hint",)),
    }
    for key, (integers, numbers, strings) in schema.items():
        if key in facts:
            error = _object(facts[key], key, integers, numbers, strings)
            if error:
                return error
    for key in ("total_days", "months"):
        if key in facts.get("span", {}) and facts["span"][key] < 1:
            return f"span.{key} 应该大于零"
    for section, key in (("span", "first_hour"), ("rhythm", "peak_hour")):
        if key in facts.get(section, {}) and facts[section][key] > 23:
            return f"{section}.{key} 应该在小时范围内"
    rhythm, words, agent = (facts.get(k, {}) for k in ("rhythm", "words", "agent"))
    if "latest" in rhythm and rhythm["latest"] is not None and not isinstance(rhythm["latest"], str):
        return "rhythm.latest 应该是字符串或 null"
    for path, data in (("sources", facts), ("tools_by_name", agent), ("hour_dist", rhythm)):
        if path in data:
            error = _counts(data[path], path)
            if error:
                return error
    if "sources" in facts and any(not re.fullmatch(r"[A-Za-z_][\w-]*", name) for name in facts["sources"]):
        return "sources 的名称应该是有效的数据源标识"
    for path, data in (("en", words), ("cn", words), ("tools_top", agent), ("busiest_titles", rhythm)):
        if path in data:
            error = _pairs(data[path], path)
            if error:
                return error
    for key in ("shortest", "longest"):
        if key in words:
            error = _object(words[key], "words." + key, integers=("len",), strings=("text",))
            if error:
                return error
            if "truncated" in words[key] and type(words[key]["truncated"]) is not bool:
                return f"words.{key}.truncated 应该是 bool"
    if "projects" in facts:
        if not isinstance(facts["projects"], list):
            return "projects 应该是数组"
        for i, project in enumerate(facts["projects"]):
            error = _project(project, f"projects.{i}")
            if error:
                return error
    if facts.get("deepest_project") is not None:
        error = _project(facts["deepest_project"], "deepest_project")
        if error:
            return error
    return ""
