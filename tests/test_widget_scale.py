"""위젯 크기 배율(widget_host.set_scale): 범위 제한·반올림·저장."""
import json
import tempfile
import unittest
from pathlib import Path

from usage_monitor import widget_host


class FakeApi:
    """창이 아직 없는 위젯 API(창 핸들 없음)."""
    _hwnd = None


class ScaleTest(unittest.TestCase):
    def setUp(self):
        self.old = widget_host.STATE_PATH
        widget_host.STATE_PATH = Path(tempfile.mkdtemp()) / "widget.json"

    def tearDown(self):
        widget_host.STATE_PATH = self.old

    def test_default_scale_is_100(self):
        self.assertEqual(widget_host.DEFAULTS["scale"], 1.0)

    def test_set_scale_clamps_rounds_and_saves(self):
        state = dict(widget_host.DEFAULTS)
        self.assertEqual(widget_host.set_scale(FakeApi(), state, 3), 2.0)        # 최대 200%
        self.assertEqual(widget_host.set_scale(FakeApi(), state, 0.1), 0.5)      # 최소 50%
        self.assertEqual(widget_host.set_scale(FakeApi(), state, 1.234), 1.23)
        saved = json.loads(widget_host.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["scale"], 1.23)

    def test_api_get_state_includes_scale(self):
        state = dict(widget_host.DEFAULTS, scale=1.5)
        api = widget_host.Api(state)
        self.assertEqual(api.get_state()["scale"], 1.5)
        self.assertEqual(api.set_scale(1.2), 1.2)

    def test_tray_menu_levels(self):
        self.assertEqual(widget_host.SCALE_LEVELS, (0.8, 1.0, 1.2, 1.5))


if __name__ == "__main__":
    unittest.main()


class SessionLinkTest(unittest.TestCase):
    """위젯 세션 줄을 누르면 앱의 그 세션으로 이동하는 링크."""

    def test_session_url_for_app_session(self):
        self.assertEqual(widget_host.session_url("local_214820fd-f41f-4612-a6b9-28e7aa9d039e"),
                         "claude://code/continue?session=local_214820fd-f41f-4612-a6b9-28e7aa9d039e")

    def test_session_url_falls_back_to_app(self):
        for bad in (None, "", "local_", "fe6c93d1-f997", "local_x&y=1", "local_" + "a" * 65):
            self.assertEqual(widget_host.session_url(bad), "claude://", bad)


class FullScreenTest(unittest.TestCase):
    """트레이 메뉴 '전체화면 대시보드'가 띄우는 브라우저 명령."""

    def test_full_command(self):
        from usage_monitor import kiosk
        cmd = kiosk.full_command(Path("C:/b/chrome.exe"), "http://127.0.0.1:4318/full", Path("C:/p/full"))
        self.assertEqual(cmd[0], str(Path("C:/b/chrome.exe")))
        self.assertIn("--start-fullscreen", cmd)
        self.assertIn("--app=http://127.0.0.1:4318/full", cmd)
        self.assertIn(f"--user-data-dir={Path('C:/p/full')}", cmd)
        self.assertNotIn("--kiosk", cmd)                       # F11·Alt+F4로 빠져나올 수 있게


class SaveStateTest(unittest.TestCase):
    """위젯 설정 저장은 잠금 + 임시 파일 교체로(Codex 리뷰)."""

    def test_save_state_is_atomic_and_locked(self):
        old = widget_host.STATE_PATH
        try:
            widget_host.STATE_PATH = Path(tempfile.mkdtemp()) / "widget.json"
            self.assertTrue(hasattr(widget_host, "_STATE_LOCK"))
            widget_host.save_state({"scale": 1.5})
            self.assertEqual(json.loads(widget_host.STATE_PATH.read_text(encoding="utf-8")), {"scale": 1.5})
            self.assertEqual([p.name for p in widget_host.STATE_PATH.parent.iterdir()], ["widget.json"])
        finally:
            widget_host.STATE_PATH = old
