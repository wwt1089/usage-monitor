"""OTLP 파싱과 스풀 tail."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from usage_monitor import db, otlp, spool
from tests import fixtures as fx


class OtlpTest(unittest.TestCase):
    def test_api_request_fields_and_pii(self):
        p = fx.payload([fx.api_request("req_1", 1791348885399)])
        [(kind, row)] = list(otlp.parse(p, "DEFAULT"))
        self.assertEqual(kind, "api_request")
        self.assertEqual(row["ts"], 1791348885399)
        self.assertEqual(row["host"], "PC1")
        self.assertEqual(row["client"], "claude-code")
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cache_read"], row["cache_creation"]),
                         (2, 300, 40000, 1000))
        self.assertEqual(row["cost_micros_est"], 200000)
        raw = json.loads(row["raw_json"])
        flat = json.dumps(raw)
        self.assertNotIn("someone@example.com", flat)
        self.assertNotIn("organization.id", flat)
        self.assertNotIn("user.", flat)

    def test_desktop_without_host_uses_default_and_drops_owner(self):
        p = fx.payload([fx.api_request("req_2", 1, host=None, effort=None, query_source="sdk")],
                       service="claude-code-desktop", host=None)
        [(_, row)] = list(otlp.parse(p, "DEFAULT"))
        self.assertEqual(row["host"], "DEFAULT")
        self.assertEqual(row["client"], "claude-code-desktop")
        self.assertIsNone(row["effort"])
        self.assertNotIn("winuser", row["raw_json"])

    def test_other_events_ignored_and_429_kept(self):
        p = fx.payload([
            fx.other_event("tool_result", 1, tool_name="Bash"),
            fx.other_event("api_error", 2, status_code=429, error="rate limited"),
            fx.other_event("api_error", 3, error="OAuth refresh token is no longer valid"),
        ])
        rows = list(otlp.parse(p, "D"))
        self.assertEqual([k for k, _ in rows], ["api_error", "api_error"])
        self.assertEqual(rows[0][1]["status_code"], 429)
        self.assertIsNone(rows[1][1]["status_code"])

    def test_user_prompt_keeps_time_and_length_not_text(self):
        p = fx.payload([fx.other_event("user_prompt", 1_791_000_000_000, **{
            "prompt.id": "p1", "prompt": "비밀 프롬프트 본문", "prompt_length": 9, "command_name": "compact"})])
        [(kind, row)] = list(otlp.parse(p, "D"))
        self.assertEqual(kind, "user_prompt")
        self.assertEqual((row["prompt_id"], row["ts"], row["prompt_length"], row["command_name"]),
                         ("p1", 1_791_000_000_000, 9, "compact"))
        self.assertNotIn("비밀", json.dumps(row, ensure_ascii=False))

    def test_api_request_prompt_id_and_ttft(self):
        rec = fx.api_request("r1", 1000)
        rec["attributes"] += fx.attrs({"prompt.id": "p1", "ttft_ms": 812})
        [(_, row)] = list(otlp.parse(fx.payload([rec]), "D"))
        self.assertEqual((row["prompt_id"], row["ttft_ms"]), ("p1", 812))


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "t.db")
        self.path = self.tmp / "statusline.jsonl"

    def tailer(self, path=None, handler=spool.ingest_statusline):
        return spool.SpoolTailer(self.conn, path or self.path, handler, "D")

    def write(self, data, mode="ab", path=None):
        with open(path or self.path, mode) as f:
            f.write(data)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM limit_observations").fetchone()[0]

    def test_partial_line_waits_for_newline(self):
        line = json.dumps(fx.statusline_line(100)).encode()
        self.write(line[:20])
        self.assertEqual(self.tailer().poll(), 0)
        self.write(line[20:] + b"\n")
        self.assertEqual(self.tailer().poll(), 1)
        self.assertEqual(self.count(), 2)  # 5h, 7d

    def test_resume_after_restart_and_truncation(self):
        self.write((json.dumps(fx.statusline_line(100)) + "\n").encode())
        self.tailer().poll()
        self.write((json.dumps(fx.statusline_line(200)) + "\n").encode())
        self.assertEqual(self.tailer().poll(), 1)  # 새 tailer(재시작)가 이어서 한 줄만 읽는다
        self.write((json.dumps(fx.statusline_line(300)) + "\n").encode(), mode="wb")  # 잘림
        self.assertEqual(self.tailer().poll(), 1)
        self.assertEqual(self.count(), 6)

    def test_bad_line_skipped(self):
        self.write(b"not json\n" + (json.dumps(fx.statusline_line(100)) + "\n").encode())
        with self.assertLogs("usage_monitor.spool", level="WARNING"):
            self.assertEqual(self.tailer().poll(), 1)

    def test_get_usage_windows_and_reset_alignment(self):
        gpath = self.tmp / "usage_tool.jsonl"
        self.write((json.dumps(fx.get_usage_line(150)) + "\n").encode(), path=gpath)
        self.write((json.dumps(fx.statusline_line(100)) + "\n").encode())
        self.tailer(gpath, spool.ingest_get_usage).poll()
        self.tailer().poll()
        rows = self.conn.execute(
            "SELECT window, source, used_pct, resets_at FROM limit_observations ORDER BY window, source").fetchall()
        got = {(r["window"], r["source"]): (r["used_pct"], r["resets_at"]) for r in rows}
        self.assertEqual(got[("5h", "get_usage")][1], got[("5h", "statusline")][1])  # 09:40:00.116Z == 1791366000
        self.assertEqual(got[("7d", "get_usage")][1], got[("7d", "statusline")][1])
        self.assertIn(("7d:fable", "get_usage"), got)
        sess = self.conn.execute("SELECT client, ctx_pct FROM session_state WHERE session_id='dt1'").fetchone()
        self.assertEqual((sess["client"], sess["ctx_pct"]), ("claude-code-desktop", 26))

    def test_get_usage_not_ok_records_health_only(self):
        gpath = self.tmp / "usage_tool.jsonl"
        rec = {"ts": 1, "host": "PC1", "session_id": "x", "source": "get_usage", "plan": None, "parse_error": True}
        self.write((json.dumps(rec) + "\n").encode(), path=gpath)
        self.tailer(gpath, spool.ingest_get_usage).poll()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.conn.execute("SELECT source FROM source_health").fetchone()[0], "get_usage")


class SessionSpoolTest(unittest.TestCase):
    """세션 상태 hook 스풀(session_events.jsonl) → live_sessions."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "t.db")
        self.path = self.tmp / "session_events.jsonl"

    def put(self, *events):
        with open(self.path, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def ev(self, event, ts, sid="s1", **kw):
        return dict({"ts": ts, "event": event, "session_id": sid, "host": "PC1"}, **kw)

    def poll(self):
        return spool.SpoolTailer(self.conn, self.path, spool.ingest_session_event, "D").poll()

    def test_session_events_build_live_row(self):
        self.put(self.ev("UserPromptSubmit", 10, title="물류B02"), self.ev("Notification", 20, ntype="permission_prompt", message="Claude needs your permission to use Bash"))
        self.assertEqual(self.poll(), 2)
        row = db.get_live(self.conn, "s1")
        self.assertEqual((row["state"], row["detail"], row["title"], row["seen"]), ("waiting", "Bash", "물류B02", 0))
        self.assertEqual([r["session_id"] for r in db.list_live(self.conn)], ["s1"])

    def test_session_event_default_host(self):
        self.put({"ts": 10, "event": "Stop", "session_id": "s2"})
        self.poll()
        self.assertEqual(db.get_live(self.conn, "s2")["host"], "D")

    def test_tailers_include_session_spool(self):
        names = [t.path.name for t in spool.tailers(self.conn, self.tmp, "D")]
        self.assertIn("session_events.jsonl", names)

    def test_mark_seen(self):
        self.put(self.ev("Stop", 10))
        self.poll()
        self.assertTrue(db.mark_seen(self.conn, "s1"))
        self.assertEqual(db.get_live(self.conn, "s1")["seen"], 1)
        self.assertFalse(db.mark_seen(self.conn, "nope"))

    def test_purge_removes_old_live_rows(self):
        now = time.time()
        self.put(self.ev("Stop", now - 8 * 86400, sid="old"), self.ev("Stop", now - 86400, sid="new"))
        self.poll()
        db.purge_old(self.conn, 90, now=now)
        self.assertIsNone(db.get_live(self.conn, "old"))
        self.assertIsNotNone(db.get_live(self.conn, "new"))

    def test_truncate_if_consumed(self):
        self.put(self.ev("Stop", 10))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write((json.dumps(self.ev("Stop", 11)) + "\n") * 90000)       # 약 5.7MB
        self.poll()
        self.assertGreater(self.path.stat().st_size, 5_000_000)
        self.assertTrue(spool.truncate_if_consumed(self.conn, self.path))
        self.assertEqual(self.path.stat().st_size, 0)
        self.put(self.ev("Notification", 20, ntype="permission_prompt", message="Claude needs your permission to use Edit"))
        self.assertEqual(self.poll(), 1)                                      # 비운 뒤 새 줄을 읽는다
        self.assertEqual(db.get_live(self.conn, "s1")["detail"], "Edit")

    def test_truncate_skips_unread_or_small(self):
        self.put(self.ev("Stop", 10))
        self.assertFalse(spool.truncate_if_consumed(self.conn, self.path))     # 아직 안 읽음
        self.poll()
        self.assertFalse(spool.truncate_if_consumed(self.conn, self.path))     # 작음
        self.assertGreater(self.path.stat().st_size, 0)
        self.assertFalse(spool.truncate_if_consumed(self.conn, self.tmp / "none.jsonl"))


if __name__ == "__main__":
    unittest.main()


class SessionSpoolValidationTest(unittest.TestCase):
    """세션 ID나 시각이 없는 줄은 건너뛴다(NULL 키 행이 화면을 깨지 않게)."""

    def test_line_without_session_id_or_ts_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "t.db")
        path = tmp / "session_events.jsonl"
        path.write_text(json.dumps({"ts": 10, "event": "Stop", "session_id": None}) + "\n"
                        + json.dumps({"event": "Stop", "session_id": "x"}) + "\n"
                        + json.dumps({"ts": 11, "event": "Stop", "session_id": "ok"}) + "\n", encoding="utf-8")
        with self.assertLogs("usage_monitor.spool", level="WARNING"):
            self.assertEqual(spool.SpoolTailer(conn, path, spool.ingest_session_event, "D").poll(), 1)
        self.assertEqual([r["session_id"] for r in db.list_live(conn)], ["ok"])


class BadLineTest(unittest.TestCase):
    """깨진 줄 하나가 스풀 처리를 멈추지 않아야 한다(Codex 리뷰)."""

    def test_bad_lines_skipped_and_good_lines_kept(self):
        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "t.db")
        path = tmp / "session_events.jsonl"
        lines = ["null", "[1, 2]",
                 json.dumps({"ts": float("nan"), "event": "Stop", "session_id": "nan"}),
                 json.dumps({"ts": 10, "event": "Notification", "session_id": "num", "ntype": "permission_prompt",
                             "message": 12345}),
                 json.dumps({"ts": 11, "event": "Stop", "session_id": "ok"})]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertLogs("usage_monitor.spool", level="WARNING"):
            spool.SpoolTailer(conn, path, spool.ingest_session_event, "D").poll()
        ids = {r["session_id"] for r in db.list_live(conn)}
        self.assertIn("ok", ids)
        self.assertNotIn("nan", ids)
        self.assertEqual(db.get_live(conn, "num")["detail"], "12345")      # 숫자 문구는 글자로 바꿔 받는다
        self.assertEqual(db.get_offset(conn, path)[1], path.stat().st_size)  # 끝까지 읽고 넘어간다

    def test_null_line_in_statusline_spool_does_not_block(self):
        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "t.db")
        path = tmp / "statusline.jsonl"
        path.write_text("null\n", encoding="utf-8")
        with self.assertLogs("usage_monitor.spool", level="WARNING"):
            spool.SpoolTailer(conn, path, spool.ingest_statusline, "D").poll()
        self.assertEqual(db.get_offset(conn, path)[1], path.stat().st_size)
