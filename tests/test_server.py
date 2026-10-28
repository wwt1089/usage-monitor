"""수신기를 임시 데이터 폴더로 띄워 HTTP 경로를 시험한다. M1 완료 기준의 자동화 부분."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import uvicorn

from usage_monitor import config, receiver as main
from tests import fixtures as fx


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Receiver:
    def __init__(self, home, **kw):
        kw.setdefault("appmeta_glob", str(Path(home) / "no-appmeta" / "*.json"))   # 실제 앱 파일을 읽지 않는다
        self.cfg = config.Config(home=home, port=free_port(), default_host="DEFAULT", spool_poll_sec=0.1, **kw)
        self.server = uvicorn.Server(uvicorn.Config(main.create_app(self.cfg), host="127.0.0.1",
                                                    port=self.cfg.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("수신기가 뜨지 않았다")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(5)

    def url(self, path):
        return f"http://127.0.0.1:{self.cfg.port}{path}"

    def post(self, path, obj, headers=None):
        req = urllib.request.Request(self.url(path), data=json.dumps(obj).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.urlopen(req, timeout=5)

    def get_json(self, path):
        return json.load(urllib.request.urlopen(self.url(path), timeout=5))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def append_spool(self, name, rec):
        (self.home / "spool").mkdir(parents=True, exist_ok=True)
        with open(self.home / "spool" / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def test_otlp_upsert_no_duplicates(self):
        now_ms = int(time.time() * 1000)
        p = fx.payload([fx.api_request("req_A", now_ms), fx.api_request("req_B", now_ms)])
        with Receiver(self.home) as r:
            self.assertEqual(r.post("/v1/logs", p).status, 200)
            r.post("/v1/logs", p)                                  # 같은 요청 재수신
            snap = r.get_json("/api/snapshot")
        self.assertEqual(snap["today"]["requests"], 2)
        self.assertEqual(snap["today"]["cost_micros_est"], 400000)
        self.assertEqual(sum(snap["burn"]["series"]["output"]), 600)
        self.assertEqual(snap["mode"], "active")
        # appmeta는 앱 정보를 못 읽어도 "읽지 못함"을 보이려고 늘 행이 있다(스펙 §7)
        self.assertEqual({h["source"] for h in snap["health"]}, {"otel", "appmeta"})

    def test_sse_pushes_spool_change_and_resumes_after_restart(self):
        now = int(time.time())
        self.append_spool("statusline.jsonl", fx.statusline_line(now - 5, pct5=10, r5=now + 3600, r7=now + 86400))
        with Receiver(self.home) as r:
            resp = urllib.request.urlopen(r.url("/stream"), timeout=5)
            first = self.read_event(resp)
            self.assertEqual(first[0], "snapshot")
            t0 = time.time()
            self.append_spool("usage_tool.jsonl", fx.get_usage_line(
                now, pct5=12, r5=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 3600))))
            while True:
                event, data = self.read_event(resp)
                if event == "snapshot" and data["limits"].get("5h", {}).get("pct") == 12:
                    break
            self.assertLess(time.time() - t0, 3)                  # 스풀 → 화면 3초 이내
            resp.close()
        # 재시작: 오프셋이 저장돼 있어 이미 읽은 줄을 다시 넣지 않는다
        self.append_spool("statusline.jsonl", fx.statusline_line(now + 1, pct5=13, r5=now + 3600, r7=now + 86400))
        with Receiver(self.home) as r:
            time.sleep(0.5)
            snap = r.get_json("/api/snapshot")
        self.assertEqual(snap["limits"]["5h"]["pct"], 13)
        self.assertEqual(sorted(snap["limits"]["5h"]["sources"]), ["get_usage", "statusline"])
        import sqlite3
        n = sqlite3.connect(self.home / "usage.db").execute(
            "SELECT COUNT(*) FROM limit_observations WHERE window='5h'").fetchone()[0]
        self.assertEqual(n, 3)

    def test_writes_cost_since_last_observation_for_hooks(self):
        now = int(time.time())
        self.append_spool("statusline.jsonl", fx.statusline_line(now - 5, pct5=10, r5=now + 3600, r7=now + 86400))
        path = self.home / "state" / "since_obs.json"
        with Receiver(self.home, obs_cost_usd=0.3) as r:
            time.sleep(0.5)                                        # 스풀의 관측을 먼저 읽게 한다
            r.post("/v1/logs", fx.payload([fx.api_request("req_A", now * 1000), fx.api_request("req_B", now * 1000)]))
            rec = {}
            for _ in range(60):
                if path.exists() and (rec := json.loads(path.read_text(encoding="utf-8")))["requests"] == 2:
                    break
                time.sleep(0.05)
        self.assertEqual(rec, {"obs_ts": now - 5, "cost_usd": 0.4, "requests": 2, "threshold_usd": 0.3})

    def test_auth_token_when_configured(self):
        with Receiver(self.home, auth_token="secret") as r:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                r.post("/v1/logs", fx.payload([]))
            self.assertEqual(cm.exception.code, 401)
            self.assertEqual(r.post("/v1/logs", fx.payload([]), {"Authorization": "Bearer secret"}).status, 200)

    # ── 세션 상태 ──────────────────────────────────────
    def wait_live(self, r, check, timeout=6):
        """조건을 만족하는 스냅샷 live가 올 때까지 기다린다."""
        live = None
        for _ in range(int(timeout / 0.1)):
            live = r.get_json("/api/snapshot")["live"]
            if check(live):
                return live
            time.sleep(0.1)
        self.fail(f"기다린 상태가 오지 않음: {live}")

    def test_seen_endpoint(self):
        self.append_spool("session_events.jsonl", {"ts": time.time(), "event": "Notification", "ntype": "permission_prompt",
                                                   "session_id": "s1", "tool": "Bash"})
        with Receiver(self.home) as r:
            self.wait_live(r, lambda l: l["unseen"]["waiting"] == 1)
            self.assertEqual(json.load(r.post("/api/sessions/s1/seen", {})), {"ok": True})
            live = self.wait_live(r, lambda l: l["unseen"]["waiting"] == 0)
            self.assertIs(live["items"][0]["seen"], True)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                r.post("/api/sessions/nope/seen", {})
            self.assertEqual(cm.exception.code, 404)

    def test_seen_endpoint_requires_token_when_configured(self):
        with Receiver(self.home, auth_token="secret") as r:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                r.post("/api/sessions/s1/seen", {})
            self.assertEqual(cm.exception.code, 401)

    def test_appmeta_marks_decision(self):
        meta_dir = self.home / "meta"
        meta_dir.mkdir(parents=True)
        t = time.time()
        self.append_spool("session_events.jsonl", {"ts": t - 2, "event": "UserPromptSubmit", "session_id": "s1"})
        self.append_spool("session_events.jsonl", {"ts": t, "event": "Stop", "session_id": "s1"})
        (meta_dir / "local_1.json").write_text(json.dumps({
            "sessionId": "local_1", "cliSessionId": "s1", "title": "앱 제목", "lastTurnEdgeAt": int(t * 1000),
            "lastAssistantUuid": "u1", "postTurnSummary": {"status_category": "blocked", "needs_action": "답변 필요",
                                                           "summarizes_uuid": "u1"}}, ensure_ascii=False), encoding="utf-8")
        with Receiver(self.home, appmeta_glob=str(meta_dir / "local_*.json")) as r:
            live = self.wait_live(r, lambda l: l["items"] and l["items"][0]["state"] == "decision")
            self.assertEqual((live["items"][0]["title"], live["items"][0]["detail"]), ("앱 제목", "답변 필요"))
            health = r.get_json("/api/snapshot")["health"]
            self.assertIn("appmeta", {h["source"] for h in health})

    def test_sessions_page_served(self):
        with Receiver(self.home) as r:
            body = urllib.request.urlopen(r.url("/sessions"), timeout=5).read().decode("utf-8")
            self.assertIn('id="live-table"', body)

    def test_appmeta_health_shown_when_nothing_readable(self):
        with Receiver(self.home) as r:                         # 기본: 없는 폴더
            for _ in range(30):
                rows = {h["source"]: h for h in r.get_json("/api/snapshot")["health"]}
                if "appmeta" in rows:
                    break
                time.sleep(0.1)
            self.assertEqual(rows["appmeta"]["status"], "idle")

    def test_full_page_served(self):
        with Receiver(self.home) as r:
            body = urllib.request.urlopen(r.url("/full"), timeout=5).read().decode("utf-8")
            self.assertIn('id="live-table"', body)
            self.assertIn('id="lim-5h"', body)

    @staticmethod
    def read_event(resp):
        event, data = None, None
        while True:
            line = resp.readline().decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and event:
                return event, data


if __name__ == "__main__":
    unittest.main()
