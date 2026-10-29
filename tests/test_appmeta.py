"""앱 세션 메타데이터(local_*.json) 읽기. 예시 파일은 실제 구조에서 키만 남긴 가짜 값이다."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from usage_monitor import appmeta, config

FIXTURE = Path(__file__).resolve().parent / "fixtures_appmeta" / "local_example.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ParseMetaTest(unittest.TestCase):
    def test_parse_meta_fields(self):
        m = appmeta.parse_meta(load_fixture())
        self.assertEqual(m, {
            "cli_session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "app_session_id": "local_11111111-2222-3333-4444-555555555555",
            "title": "예시 세션 제목", "status_category": "blocked",
            "needs_action": "도구 설치를 승인할지 답변", "summary_fresh": True,
            "last_turn_edge_ms": 1793149223682, "last_focused_ms": 1793149211461, "archived": False,
        })

    def test_parse_meta_missing_cli_id_is_none(self):
        d = load_fixture()
        d.pop("cliSessionId")
        self.assertIsNone(appmeta.parse_meta(d))

    def test_parse_meta_stale_summary(self):
        d = load_fixture()
        d["lastAssistantUuid"] = "other"
        self.assertIs(appmeta.parse_meta(d)["summary_fresh"], False)
        d.pop("postTurnSummary")
        m = appmeta.parse_meta(d)
        self.assertEqual((m["summary_fresh"], m["status_category"], m["needs_action"]), (False, None, None))

    def test_parse_meta_missing_numbers_are_zero(self):
        d = load_fixture()
        d.pop("lastFocusedAt")
        d.pop("lastTurnEdgeAt")
        m = appmeta.parse_meta(d)
        self.assertEqual((m["last_focused_ms"], m["last_turn_edge_ms"]), (0, 0))


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / "Claude-Data" / "x" / "claude-code-sessions" / "a" / "b"
        self.dir.mkdir(parents=True)
        self.pattern = str(self.tmp / "Claude-Data" / "*" / "claude-code-sessions" / "*" / "*" / "local_*.json")

    def put(self, name, cli_id, **kw):
        d = dict(load_fixture(), cliSessionId=cli_id, **kw)
        path = self.dir / name
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return path

    def test_reader_picks_up_changes_only(self):
        p1 = self.put("local_1.json", "s1")
        self.put("local_2.json", "s2")
        r = appmeta.AppMetaReader(self.pattern)
        self.assertTrue(r.poll(100))
        self.assertEqual(sorted(r.cache), ["s1", "s2"])
        self.assertEqual(r.files_seen, 2)
        self.assertFalse(r.poll(102))
        self.put("local_1.json", "s1", title="바뀐 제목")
        st = p1.stat()
        os.utime(p1, (st.st_atime, st.st_mtime + 5))
        self.assertTrue(r.poll(104))
        self.assertEqual(r.cache["s1"]["title"], "바뀐 제목")

    def test_partial_json_retried_next_poll(self):
        path = self.dir / "local_3.json"
        path.write_text('{"cliSessionId": "s3", "title": "반쯤', encoding="utf-8")
        r = appmeta.AppMetaReader(self.pattern)
        self.assertFalse(r.poll(100))
        self.assertNotIn("s3", r.cache)
        self.put("local_3.json", "s3")
        st = path.stat()
        os.utime(path, (st.st_atime, st.st_mtime + 5))
        self.assertTrue(r.poll(102))
        self.assertIn("s3", r.cache)

    def test_new_file_found_after_rescan(self):
        r = appmeta.AppMetaReader(self.pattern, rescan_sec=60)
        self.assertFalse(r.poll(100))
        self.put("local_4.json", "s4")
        self.assertFalse(r.poll(110))          # 다시 찾기 전에는 모른다
        self.assertTrue(r.poll(161))
        self.assertIn("s4", r.cache)

    def test_reader_missing_dir(self):
        r = appmeta.AppMetaReader(str(self.tmp / "none" / "*.json"))
        self.assertFalse(r.poll(100))
        self.assertEqual(r.files_seen, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ConfigTest(unittest.TestCase):
    def test_appmeta_config_defaults_and_toml(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertIs(config.Config(home=tmp).appmeta_enabled, True)
        (tmp / "config.toml").write_text('appmeta_enabled = "false"\nappmeta_glob = "D:/x/*.json"\n', encoding="utf-8")
        cfg = config.load(tmp / "config.toml")
        self.assertEqual((cfg.appmeta_enabled, cfg.appmeta_glob), (False, "D:/x/*.json"))

    def test_default_glob_uses_localappdata(self):
        old = os.environ.get("LOCALAPPDATA")
        try:
            os.environ["LOCALAPPDATA"] = r"C:\Users\x\AppData\Local"
            g = appmeta.default_glob()
            self.assertTrue(g.startswith(r"C:\Users\x\AppData\Local"))
            self.assertTrue(g.endswith("local_*.json"))
            self.assertIn("claude-code-sessions", g)
        finally:
            if old is not None:
                os.environ["LOCALAPPDATA"] = old


if __name__ == "__main__":
    unittest.main()


class ReadableCountTest(unittest.TestCase):
    def test_files_without_cli_id_do_not_count_as_readable(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "local_1.json").write_text(json.dumps({"sessionId": "local_1", "title": "x"}), encoding="utf-8")
        r = appmeta.AppMetaReader(str(tmp / "local_*.json"))
        r.poll(100)
        self.assertEqual(r.files_seen, 0)
        r.poll(102)                                # 서명은 기억하되 다시 읽어도 0
        self.assertEqual(r.files_seen, 0)


class RobustMetaTest(unittest.TestCase):
    """앱 메타데이터: 잘못된 타입·사라진 파일 정리(Codex 리뷰)."""

    def test_non_string_cli_id_is_none(self):
        d = load_fixture()
        d["cliSessionId"] = ["x"]
        self.assertIsNone(appmeta.parse_meta(d))

    def test_removed_file_drops_cache_entry(self):
        tmp = Path(tempfile.mkdtemp())
        p1 = tmp / "local_1.json"
        p1.write_text(json.dumps(dict(load_fixture(), cliSessionId="s1")), encoding="utf-8")
        r = appmeta.AppMetaReader(str(tmp / "local_*.json"), rescan_sec=0)
        r.poll(100)
        self.assertIn("s1", r.cache)
        p1.unlink()
        self.assertTrue(r.poll(101))
        self.assertNotIn("s1", r.cache)


class WithTokenTest(unittest.TestCase):
    """후속 리뷰: 화면·트레이가 수신기를 부를 때 토큰을 붙인다(설정했을 때만)."""

    def test_with_token(self):
        self.assertEqual(config.with_token("http://h/full", ""), "http://h/full")
        self.assertEqual(config.with_token("http://h/full", "a b&c"), "http://h/full?token=a+b%26c")
        self.assertEqual(config.with_token("http://h/web/widget.html?size=l", "t"), "http://h/web/widget.html?size=l&token=t")
