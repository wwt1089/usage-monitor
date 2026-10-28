"""트레이 알림 로직(alerts.py): 토스트 결정·묶음·완료 지연·배지·SSE 파싱."""
import unittest

from usage_monitor import alerts


def item(sid, state, ts, seen=False, title=None, detail=None):
    """스냅샷 live.items 한 항목."""
    return {"session_id": sid, "title": title or sid, "cwd": None, "state": state, "detail": detail,
            "state_ts": ts, "last_event_ts": ts, "seen": seen, "stale": False}


def live(**unseen):
    """스냅샷 live 중 배지·툴팁이 쓰는 부분."""
    u = {"waiting": 0, "decision": 0, "done": 0, "error": 0}
    u.update(unseen)
    return {"items": [], "counts": dict(u, working=0), "unseen": u}


class SseTest(unittest.TestCase):
    def test_parse_sse_snapshot_and_heartbeat(self):
        lines = ["event: snapshot", 'data: {"now": 1}', "", "event: heartbeat", 'data: {"now": 2}', "",
                 "event: snapshot", "data: {broken", ""]
        self.assertEqual(list(alerts.parse_sse(lines)), [("snapshot", {"now": 1}), ("heartbeat", {"now": 2})])


class BadgeTest(unittest.TestCase):
    def test_badge_alert_done_none(self):
        self.assertEqual(alerts.badge(live(waiting=1, decision=1)), ("alert", "2"))
        self.assertEqual(alerts.badge(live(error=1)), ("alert", "1"))
        self.assertEqual(alerts.badge(live(done=3)), ("done", ""))
        self.assertEqual(alerts.badge(live()), (None, ""))
        self.assertEqual(alerts.badge(None), (None, ""))

    def test_badge_caps_at_9_plus(self):
        self.assertEqual(alerts.badge(live(waiting=12)), ("alert", "9+"))

    def test_tooltip_part_skips_zero(self):
        lv = {"counts": {"working": 1, "waiting": 2, "decision": 0, "done": 3, "error": 1}}
        self.assertEqual(alerts.tooltip_part(lv), "대기 2 · 오류 1 · 완료 3 · 작업 중 1")
        self.assertEqual(alerts.tooltip_part({"counts": {}}), "")


class TrackerTest(unittest.TestCase):
    """세션 Windows 알림(토스트)은 뺐다. 트래커는 '숨긴 위젯을 띄울지'만 정한다."""

    def test_no_toasts_anymore(self):
        from usage_monitor import tray
        self.assertFalse(hasattr(alerts.AlertTracker(), "pop_toasts"))
        self.assertFalse(hasattr(tray.App, "toast_loop"))

    def test_initial_snapshot_does_not_wake(self):
        t = alerts.AlertTracker()
        self.assertFalse(t.update([item("s1", "waiting", 100)], 1000))

    def test_new_waiting_or_decision_wakes_widget(self):
        t = alerts.AlertTracker()
        t.update([], 1000)
        self.assertTrue(t.update([item("s1", "waiting", 1001)], 1001))
        self.assertTrue(t.update([item("s2", "decision", 1002)], 1002))

    def test_done_error_seen_or_repeat_do_not_wake(self):
        t = alerts.AlertTracker()
        t.update([], 1000)
        self.assertFalse(t.update([item("d", "done", 1001)], 1001))
        self.assertFalse(t.update([item("e", "error", 1001)], 1001))
        self.assertFalse(t.update([item("w", "waiting", 1001, seen=True)], 1001))
        t.update([item("x", "waiting", 1002)], 1002)
        self.assertFalse(t.update([item("x", "waiting", 1002)], 1003))


class TrayIconTest(unittest.TestCase):
    """트레이 아이콘 배지 그림(tray.make_icon)."""

    @staticmethod
    def orange(px):
        r, g, b = px[:3]
        return r > 200 and 100 < g < 180 and b < 80

    def test_make_icon_badge_pixels(self):
        from usage_monitor import tray
        # (45, 5)는 배지 원 안쪽이면서 개수 글자가 닿지 않는 자리
        self.assertTrue(self.orange(tray.make_icon(42, ("alert", "3")).getpixel((45, 5))))
        self.assertFalse(self.orange(tray.make_icon(42).getpixel((45, 5))))
        r, g, b = tray.make_icon(42, ("done", "")).getpixel((56, 8))[:3]
        self.assertTrue(g > 120 and r < 80)                                  # 초록 점

    def test_tooltip_includes_live_and_is_capped(self):
        from usage_monitor import tray
        snap = {"limits": {}, "live": {"counts": {"waiting": 2, "done": 1}}}
        self.assertEqual(tray.tooltip(snap), "Usage Monitor · 한도 관측 없음 · 대기 2 · 완료 1")
        snap["limits"] = {"5h": {"pct": 42, "verdict": "ok"}}
        snap["live"]["counts"] = {"waiting": 99999999999999999999, "done": 99999999999999999999,
                                  "error": 99999999999999999999, "working": 10 ** 60}
        self.assertLessEqual(len(tray.tooltip(snap)), 127)


class TrayStateTest(unittest.TestCase):
    """트레이 상태 갱신(Codex 리뷰): SSE로도 한도 숫자, 기억 목록은 지금 것만."""

    def test_pct_from_snapshot(self):
        from usage_monitor import tray
        self.assertEqual(tray.pct_from_snapshot({"limits": {"5h": {"pct": 42, "verdict": "ok"}}}), 42)
        self.assertIsNone(tray.pct_from_snapshot({"limits": {"5h": {"pct": 42, "verdict": "reset"}}}))
        self.assertIsNone(tray.pct_from_snapshot(None))

    def test_tracker_remembers_only_current_keys(self):
        t = alerts.AlertTracker()
        t.update([item("a", "done", 1)], 1)
        t.update([item("b", "working", 2)], 2)
        self.assertEqual(t.known, {("b", "working", 2)})
