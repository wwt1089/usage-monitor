"""ETA·burn 계산."""
import tempfile
import unittest
from pathlib import Path

from usage_monitor import db, metrics

R5 = 1_800_000_000          # 5h 창 리셋 시각
START5 = R5 - 5 * 3600


class LimitStateTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")

    def obs(self, ts, pct, session="s", source="statusline", window="5h", resets_at=R5):
        db.insert_observation(self.conn, ts, "PC1", session, source, window, pct, resets_at)

    def test_trend_when_enough_recent_observations(self):
        now = START5 + 3600
        for i, pct in enumerate((10, 12, 14, 16)):           # 10분에 2%p → 시간당 12%p
            self.obs(now - 1800 + i * 600, pct)
        s = metrics.limit_state(self.conn, "5h", now)
        self.assertEqual(s["method"], "trend")
        self.assertAlmostEqual(s["rate_pct_per_hour"], 12.0, places=3)
        self.assertEqual(s["verdict"], "safe")               # 남은 84%p / 12 = 7h > 리셋까지 4h

    def test_window_average_when_values_flat(self):
        now = START5 + 2 * 3600
        for i in range(5):
            self.obs(now - 1200 + i * 60, 40)                 # 변화 없음 → 기울기 0
        s = metrics.limit_state(self.conn, "5h", now)
        self.assertEqual(s["method"], "window_avg")
        t_last = now - 1200 + 240
        self.assertAlmostEqual(s["rate_pct_per_hour"], 40 / ((t_last - START5) / 3600), places=6)
        self.assertEqual(s["last_obs_ts"], t_last)

    def test_window_average_when_single_old_observation(self):
        now = START5 + 4 * 3600
        self.obs(START5 + 3600, 60)                           # 1시간에 60% → 남은 40%는 40분 뒤
        s = metrics.limit_state(self.conn, "5h", now)
        self.assertEqual(s["method"], "window_avg")
        self.assertEqual(s["verdict"], "exhaust_before_reset")
        self.assertEqual(s["eta_ts"], START5 + 3600 + 2400)

    def test_running_max_ignores_late_lower_value(self):
        now = START5 + 3600
        self.obs(now - 300, 30, session="new")
        self.obs(now - 60, 25, session="old")                 # 오래된 세션이 늦게 보낸 낮은 값
        s = metrics.limit_state(self.conn, "5h", now)
        self.assertEqual(s["pct"], 30)
        self.assertEqual(s["last_obs_ts"], now - 60)

    def test_no_usage_and_reset_and_exhausted(self):
        self.obs(START5 + 60, 0)
        self.assertEqual(metrics.limit_state(self.conn, "5h", START5 + 120)["verdict"], "no_usage")
        self.assertEqual(metrics.limit_state(self.conn, "5h", R5 + 1)["verdict"], "reset")
        self.obs(START5 + 600, 100)
        self.assertEqual(metrics.limit_state(self.conn, "5h", START5 + 700)["verdict"], "exhausted")

    def test_seven_day_uses_week_length(self):
        r7 = R5 + 3 * 86400
        self.obs(r7 - 4 * 86400, 16, window="7d", resets_at=r7)   # 창 시작 후 3일에 16%
        s = metrics.limit_state(self.conn, "7d", r7 - 4 * 86400 + 60)
        self.assertEqual(s["method"], "window_avg")
        self.assertAlmostEqual(s["rate_pct_per_hour"], 16 / 72, places=6)


class SinceObservationTest(unittest.TestCase):
    def test_counts_only_requests_after_last_observation(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
        t_obs = START5 + 3600
        self.assertIsNone(metrics.since_observation(conn))
        db.insert_observation(conn, t_obs - 600, "PC1", "s", "get_usage", "5h", 2, R5)
        db.insert_observation(conn, t_obs, "PC1", "s", "get_usage", "5h", 4, R5)
        base = dict(host="PC1", client=None, session_id="s", model="m", effort=None, speed=None, query_source=None,
                    input_tokens=1, output_tokens=1, cache_read=0, cache_creation=0, duration_ms=1, raw_json="{}")
        for rid, ts, cost in (("a", t_obs - 10, 5_000_000), ("b", t_obs + 10, 700_000), ("c", t_obs + 20, 300_000)):
            db.upsert_request(conn, dict(base, request_id=rid, ts=ts * 1000, cost_micros_est=cost))
        expected = {"cost_micros_est": 1_000_000, "requests": 2}
        self.assertEqual(metrics.since_observation(conn), {"obs_ts": t_obs, **expected})
        self.assertEqual(metrics.limit_state(conn, "5h", t_obs + 60)["since_obs"], expected)


class BurnTest(unittest.TestCase):
    def test_minute_buckets_sum_all_hosts(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
        now = 1_800_000_000 + 30
        base = dict(client=None, session_id="s", model="m", effort=None, speed=None, query_source=None,
                    cost_micros_est=1, duration_ms=1, raw_json="{}")
        for rid, ts, host, toks in (("a", now - 10, "PC1", (1, 2, 3, 4)), ("b", now - 5, "PC2", (10, 20, 30, 40)),
                                    ("c", now - 120, "PC1", (5, 5, 5, 5))):
            db.upsert_request(conn, dict(base, request_id=rid, ts=ts * 1000, host=host, input_tokens=toks[0],
                                         output_tokens=toks[1], cache_read=toks[2], cache_creation=toks[3]))
        b = metrics.burn(conn, now)
        self.assertEqual(b["series"]["output"][-1], 22)
        self.assertEqual(b["series"]["input"][-3], 5)
        self.assertEqual(len(b["series"]["cache_read"]), 60)


if __name__ == "__main__":
    unittest.main()


class LiveSnapshotTest(unittest.TestCase):
    """스냅샷의 세션 상태(live) 항목."""

    def setUp(self):
        self.conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
        self.now = 1_800_000_000

    def put(self, sid, state, ago, seen, archived=0, title=None):
        t = self.now - ago
        db.put_live(self.conn, {"session_id": sid, "app_session_id": None, "host": "PC1", "title": title,
                                "cwd": "C:\\PDD\\" + sid, "state": state, "detail": None, "state_ts": t,
                                "last_event_ts": t, "stop_ts": None, "seen": seen, "archived": archived})

    def test_live_snapshot_filters_sorts_counts(self):
        self.put("w", "waiting", 60, 0, title="대기 세션")
        self.put("k", "working", 30, 1)
        self.put("old", "done", 3 * 3600, 1)
        self.put("arch", "decision", 10, 0, archived=1)
        live = metrics.live_snapshot(self.conn, self.now)
        self.assertEqual([i["session_id"] for i in live["items"]], ["w", "k"])
        self.assertEqual(live["counts"], {"working": 1, "waiting": 1, "decision": 0, "done": 0, "error": 0})
        self.assertEqual(live["unseen"], {"waiting": 1, "decision": 0, "done": 0, "error": 0})
        item = live["items"][0]
        self.assertEqual(set(item), {"session_id", "app_session_id", "title", "cwd", "state", "detail",
                                     "state_ts", "last_event_ts", "seen", "stale"})
        self.assertIs(item["seen"], False)
        self.assertEqual(item["title"], "대기 세션")

    def test_archived_unseen_not_counted(self):
        self.put("arch", "waiting", 10, 0, archived=1)
        live = metrics.live_snapshot(self.conn, self.now)
        self.assertEqual((live["items"], live["unseen"]["waiting"], live["counts"]["waiting"]), ([], 0, 0))

    def test_stale_flag(self):
        self.put("k", "working", 2000, 1)
        self.assertIs(metrics.live_snapshot(self.conn, self.now)["items"][0]["stale"], True)

    def test_build_snapshot_has_live(self):
        self.assertIn("live", metrics.build_snapshot(self.conn, self.now))


class HealthReviewTest(unittest.TestCase):
    """원작자 리뷰: 앱 정보(appmeta) 갱신이 OTel을 '수집 끊김 의심'으로 만들지 않게."""

    def test_appmeta_does_not_mark_otel_missing(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
        now = 1_800_000_000
        db.touch_health(conn, "PC1", "otel", now - 600)          # 10분 전 마지막 요청(유휴)
        db.touch_health(conn, "PC1", "appmeta", now - 1)         # 앱 정보는 2초마다 갱신된다
        st = {r["source"]: r["status"] for r in metrics.health(conn, now)}
        self.assertEqual(st["otel"], "idle")
