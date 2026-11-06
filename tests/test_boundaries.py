"""异常输入、发布失败和并发竞争回归；只使用独立临时目录。"""

import copy
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cli
import engine
import fileio
import render
from adapters import dsh
from adapters.base import LoadReport, Message, Session


def fixture():
    session = Session("fixture", "项目", "dsh", msgs=[
        Message("统计报表检查", datetime(2026, 1, 1, 2, tzinfo=timezone.utc)),
        Message("谢谢，先别继续，统计报表检查", datetime(2026, 1, 2, 2, tzinfo=timezone.utc)),
    ], tools=Counter({"read": 7, "write": 2}), reasoning_chars=150,
        approvals_asked=4, approvals_granted=1, approvals_denied=1,
        approvals_cancelled=1, approvals_pending=1)
    return session, json.loads(json.dumps(engine.build_facts([session])))


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="agenttime-boundary-")
        self.addCleanup(self.tmp.cleanup)
        self.wd = Path(self.tmp.name)
        self.session, self.facts = fixture()

    def run_render(self, facts=None, narrative=None, output="report.html", raw_facts=None, raw_narrative=None):
        for name, value, raw in (("facts.json", self.facts if facts is None else facts, raw_facts),
                                 ("narrative.json", {"essay": ["x"]} if narrative is None else narrative, raw_narrative)):
            (self.wd / name).write_bytes(raw if raw is not None else json.dumps(value, ensure_ascii=True).encode())
        return subprocess.run([sys.executable, "-B", str(ROOT / "cli.py"), "render",
                               "facts.json", "narrative.json", "--out", output],
                              cwd=self.wd, capture_output=True, encoding="utf-8", errors="replace")

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_explicit_failure_preserves_input_and_existing_fallback(self):
        old = self.wd / "report.html"
        old.write_text("KEEP", encoding="utf-8")
        result = self.run_render(output="nonexistent/facts.json")
        self.assert_rejected(result)
        self.assertEqual(json.loads((self.wd / "facts.json").read_text(encoding="utf-8")), self.facts)
        self.assertEqual(old.read_text(encoding="utf-8"), "KEEP")
        result = self.run_render(output="nonexistent/report.html")
        self.assert_rejected(result)
        self.assertEqual(old.read_text(encoding="utf-8"), "KEEP")

    def test_cannot_replace_input(self):
        result = self.run_render(output="facts.json")
        self.assert_rejected(result)
        self.assertEqual(json.loads((self.wd / "facts.json").read_text(encoding="utf-8")), self.facts)

    def test_default_fallback_still_works_without_replacing_existing_file(self):
        (self.wd / "report.html").write_text("KEEP", encoding="utf-8")
        script = ("import sys; sys.path.insert(0," + repr(str(ROOT)) + "); import render; "
                  "result=render.write_out('NEW','missing/report.html',allow_overwrite=False); "
                  "raise SystemExit(0 if result else 1)")
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=self.wd, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.wd / "report.html").read_text(encoding="utf-8"), "KEEP")
        self.assertEqual((self.wd / "report_2.html").read_text(encoding="utf-8"), "NEW")

    def test_cannot_replace_input_hardlink(self):
        self.run_render()
        alias = self.wd / "alias.html"
        try:
            os.link(self.wd / "facts.json", alias)
        except PermissionError:
            self.skipTest("Current sandbox does not permit hardlinks")
        result = self.run_render(output="alias.html")
        self.assert_rejected(result)
        self.assertEqual(json.loads(alias.read_text(encoding="utf-8")), self.facts)

    def test_parallel_default_writes_keep_both_complete_files(self):
        target = self.wd / "same.html"
        barrier = threading.Barrier(2)
        publish_method = "rename" if os.name == "nt" else "link"
        original_publish = getattr(os, publish_method)
        results, errors = [], []
        def synchronized_publish(source, destination):
            if Path(destination) == target:
                barrier.wait(timeout=5)
            return original_publish(source, destination)
        def write(text):
            try:
                results.append(render.write_out(text, str(target), allow_overwrite=False))
            except Exception as exc:
                errors.append(exc)
        with patch.object(fileio.os, publish_method, side_effect=synchronized_publish):
            threads = [threading.Thread(target=write, args=(value,)) for value in ("FIRST", "SECOND")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 2)
        self.assertEqual({Path(path).read_text(encoding="utf-8") for path in results}, {"FIRST", "SECOND"})

    def test_publish_failure_keeps_old_file_and_cleans_temporary(self):
        target = self.wd / "report.html"
        target.write_text("KEEP", encoding="utf-8")
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation), patch.object(fileio.os, operation, side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError):
                    fileio.atomic_write_text(str(target), "NEW")
            self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")
            self.assertEqual(list(self.wd.glob(".agenttime-*.tmp")), [])

    def test_surrogate_and_invalid_encoding_preserve_existing_report(self):
        target = self.wd / "report.html"
        target.write_text("KEEP", encoding="utf-8")
        for raw in (b'{"essay":["\\ud800"]}', b'\xff'):
            result = self.run_render(raw_narrative=raw)
            self.assert_rejected(result)
            self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")
        result = self.run_render(raw_facts=b'\xff')
        self.assert_rejected(result)
        self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")

    def test_nested_bad_types_and_invalid_counts_are_rejected(self):
        cases = [("span", "bad"), ("sources", ["dsh"]), ("sources", {"dsh": "bad"})]
        for key, value in cases:
            facts = copy.deepcopy(self.facts); facts[key] = value
            with self.subTest(key=key, value=value):
                self.assert_rejected(self.run_render(facts=facts))
        for value in ({"n": 7}, True, -7, 1.5, float("nan"), float("inf")):
            facts = copy.deepcopy(self.facts); facts["volume"]["msgs"] = value
            with self.subTest(count=repr(value)):
                self.assert_rejected(self.run_render(facts=facts, narrative={"essay": ["{volume.msgs|round}"]}))
        facts = copy.deepcopy(self.facts); facts["span"]["months"] = True
        self.assert_rejected(self.run_render(facts=facts))

    def test_bad_filters_fail_but_prose_braces_and_literal_fact_braces_survive(self):
        for value in ("{volume.msgs|COMMA}", "{volume.msgs|commma}", "{volume.msgs|comma,}",
                      "{volume . msgs}", "{volume[msgs]}"):
            with self.subTest(value=value):
                self.assert_rejected(self.run_render(narrative={"essay": [value]}))
        facts = copy.deepcopy(self.facts); facts["span"]["first_text"] = "{volume.msgs|COMMA}"
        result = self.run_render(facts=facts, narrative={"essay": ["{x: int} {a+b} {span.first_text}"]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("{volume.msgs|COMMA}", (self.wd / "report.html").read_text(encoding="utf-8"))

    def test_unsupported_filter_combinations_name_path_filters_and_reason(self):
        # 不合规的过滤器组合必须报错，而且要能照着改：路径、用了哪个过滤器、为什么不行
        facts = copy.deepcopy(self.facts)
        facts["volume"]["avg_chars"] = 82.5
        cases = [
            ("{volume.avg_chars|cnwan}", "volume.avg_chars（cnwan）", "先加 |round"),
            ("{volume.avg_chars|comma|cnwan}", "volume.avg_chars（comma|cnwan）", "不能同时用"),
            ("{span.first_text|comma}", "span.first_text（comma）", "数字过滤器"),
        ]
        for text, location, reason in cases:
            with self.subTest(text=text):
                result = self.run_render(facts=facts, narrative={"essay": [text]})
                self.assert_rejected(result)
                output = result.stdout + result.stderr
                self.assertIn(location, output)
                self.assertIn(reason, output)

    def test_missing_cards_fields_duplicates_and_corrupt_project_rows(self):
        valid = {"idx": "01", "kicker": "k", "aside": "x"}
        for card in ({}, {"idx": "01"}, {"idx": "01", "kicker": "k"},
                     {"idx": "01", "kicker": "k", "aside": " "}):
            with self.subTest(card=card):
                self.assert_rejected(self.run_render(narrative={"cards": [card]}))
        self.assert_rejected(self.run_render(narrative={"cards": [valid, valid]}))
        facts = copy.deepcopy(self.facts); facts["projects"].insert(0, "bad")
        self.assert_rejected(self.run_render(facts=facts, narrative={"cards": [{**valid, "list_from": "projects"}]}))
        facts["projects"] = [{"title": "p", "start": "a"}]
        self.assert_rejected(self.run_render(facts=facts, narrative={"cards": [{**valid, "list_from": "projects"}]}))

    def test_full_example_still_renders_thirteen_cards(self):
        narrative = json.loads((ROOT / "narrative.example.json").read_text(encoding="utf-8"))
        result = self.run_render(narrative=narrative)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.wd / "report.html").read_text(encoding="utf-8").count('<section class="card"'), 13)

    def test_doctor_all_unreadable_and_no_dated_messages_return_nonzero(self):
        for sessions, report in (([], LoadReport(dirs=1, read_errors=1)),
                                 ([Session("x", "x", "dsh", msgs=[Message("x")])], LoadReport(ok=1))):
            with patch.object(cli, "available", return_value=[("dsh", "fixture")]), \
                 patch.object(cli, "load", return_value=(sessions, report)), redirect_stdout(io.StringIO()) as captured:
                rc = cli.cmd_doctor(None)
            self.assertNotEqual(rc, 0)
            self.assertNotIn("默认使用", captured.getvalue())

    def test_doctor_dependency_failure_does_not_blame_timestamps(self):
        with patch.object(cli, "available", return_value=[("dsh", "fixture")]), \
             patch.object(cli, "load", side_effect=RuntimeError("读 DSH 会话需要 zstandard：pip install zstandard")), \
             redirect_stdout(io.StringIO()) as captured:
            rc = cli.cmd_doctor(None)
        self.assertNotEqual(rc, 0)
        self.assertIn("pip install zstandard", captured.getvalue())
        self.assertIn("未能读取", captured.getvalue())
        self.assertNotIn("时间戳", captured.getvalue())

    def test_doctor_empty_data_and_missing_timestamps_have_distinct_hints(self):
        cases = [([], "没有读到真人消息"),
                 ([Session("x", "x", "dsh", msgs=[Message("x")])], "没有可用时间戳")]
        for sessions, expected in cases:
            with self.subTest(expected=expected), \
                 patch.object(cli, "available", return_value=[("dsh", "fixture")]), \
                 patch.object(cli, "load", return_value=(sessions, LoadReport())), \
                 redirect_stdout(io.StringIO()) as captured:
                self.assertNotEqual(cli.cmd_doctor(None), 0)
            self.assertIn(expected, captured.getvalue())

    def test_doctor_surfaces_skipped_malformed_events(self):
        # 读得了、能统计，但形状异常的事件被跳过——这条也必须出现在 doctor 里，
        # 否则「严格判定」和「静默丢数据」从输出上看不出区别。
        sessions = [Session("x", "x", "dsh", msgs=[
            Message("hi", datetime(2026, 1, 1, 2, tzinfo=timezone.utc))])]
        report = LoadReport(ok=1, malformed_events=2,
                            errors=["session.v4.jsonl.zstd: 第 2 行不是合法 JSON"])
        with patch.object(cli, "available", return_value=[("dsh", "fixture")]), \
             patch.object(cli, "load", return_value=(sessions, report)), \
             redirect_stdout(io.StringIO()) as captured:
            rc = cli.cmd_doctor(None)
        out = captured.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("跳过格式异常的事件 2 条", out)
        self.assertIn("第 2 行不是合法 JSON", out)
        self.assertNotIn("读不出来", out)

    def test_scan_io_failure_preserves_previous_facts(self):
        target = self.wd / "facts.json"
        target.write_text("KEEP", encoding="utf-8")
        with patch.object(cli, "available", return_value=[("dsh", "fixture")]), \
             patch.object(cli, "load", return_value=([self.session], LoadReport(ok=1))), \
             patch.object(fileio.os, "fsync", side_effect=OSError("simulated disk full")), \
             redirect_stdout(io.StringIO()) as captured:
            rc = cli.cmd_scan(SimpleNamespace(source="all", out=str(target)))
        self.assertNotEqual(rc, 0)
        self.assertIn("未替换", captured.getvalue())
        self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")
        self.assertEqual(list(self.wd.glob(".agenttime-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing violation")
    def test_real_file_lock_does_not_overwrite_fallback(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        (self.wd / "locked").mkdir()
        target = self.wd / "locked" / "report.html"
        target.write_text("TARGET", encoding="utf-8")
        fallback = self.wd / "report.html"; fallback.write_text("KEEP", encoding="utf-8")
        handle = kernel.CreateFileW(str(target), 0xC0000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.assert_rejected(self.run_render(output="locked/report.html"))
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(target.read_text(encoding="utf-8"), "TARGET")
        self.assertEqual(fallback.read_text(encoding="utf-8"), "KEEP")

    def test_malformed_sources_are_not_legacy_messages(self):
        try:
            import zstandard as zstd
        except ImportError:
            self.skipTest("zstandard unavailable")
        log = self.wd / "p" / "session-test"; log.mkdir(parents=True)
        events = []
        for source in ("system", None, [], {}, {"kind": None}):
            events.append({"type": "user/message", "time": 1767225600000,
                           "data": {"source": source, "content": [{"type": "text", "text": "INJECTED"}]}})
        events.append({"type": "user/message", "time": 1767225600000,
                       "data": {"content": [{"type": "text", "text": "legacy"}]}})
        payload = b"".join(zstd.ZstdCompressor().compress((json.dumps(event) + "\n").encode()) for event in events)
        (log / "session.v4.jsonl.zstd").write_bytes(payload)
        sessions, report = dsh.load(str(self.wd))
        self.assertEqual([message.text for session in sessions for message in session.msgs], ["legacy"])
        self.assertEqual(report.malformed_events, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
