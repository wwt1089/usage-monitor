"""hook 스크립트를 실제로 실행해 본다. USAGE_MONITOR_HOME을 임시 폴더로 바꿔 실제 스풀을 건드리지 않는다."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "usage_monitor" / "hooks"
sys.path.insert(0, str(HOOKS))
import usage_hook_common as c  # noqa: E402


def run(script, payload, home, entrypoint="claude-desktop", flags=("-S",), **env):
    e = dict(os.environ, USAGE_MONITOR_HOME=str(home), CLAUDE_CODE_ENTRYPOINT=entrypoint, **env)
    p = subprocess.run([sys.executable, *flags, str(HOOKS / script)], input=json.dumps(payload).encode(),
                       capture_output=True, env=e, timeout=20)
    return p.stdout.decode("utf-8")


class IntervalTest(unittest.TestCase):
    def test_tiers(self):
        now = 1000
        lim = lambda p5, p7: {"ts": 0, "5h": (p5, now + 9999), "7d": (p7, now + 9999)}
        self.assertEqual(c.observation_interval(None, now), 0)
        self.assertEqual(c.observation_interval(lim(10, 20), now), 3600)
        self.assertEqual(c.observation_interval(lim(55, 20), now), 1200)
        self.assertEqual(c.observation_interval(lim(10, 80), now), 1200)
        self.assertEqual(c.observation_interval(lim(85, 20), now), 300)
        self.assertEqual(c.observation_interval(lim(10, 95), now), 300)
        self.assertEqual(c.observation_interval({"ts": 0, "5h": (10, now - 1)}, now), 0)  # 리셋 지남


class HookRunTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.spool = self.home / "spool"

    def put_get_usage(self, ts, pct5):
        self.spool.mkdir(parents=True, exist_ok=True)
        resets = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 3 * 3600))
        rec = {"ts": ts, "source": "get_usage", "plan": {"status": "ok", "windows": [
            {"label": "5-hour limit", "percentUsed": pct5, "resetsAt": resets},
            {"label": "Weekly · all models", "percentUsed": 10, "resetsAt": resets}]}}
        with open(self.spool / "usage_tool.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def test_nudge_only_for_desktop_and_respects_cooldown(self):
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "a"}, self.home, entrypoint="cli"), "")
        out = run("usage_prompt_nudge.py", {"session_id": "a"}, self.home)
        self.assertIn("additionalContext", out)
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "b"}, self.home), "")

    def test_low_usage_waits_an_hour_high_usage_five_minutes(self):
        now = int(time.time())
        self.put_get_usage(now - 1500, 10)                     # 25분 전, 10% → 60분 간격
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "a"}, self.home), "")
        self.put_get_usage(now - 400, 85)                      # 약 7분 전, 85% → 5분 간격
        self.assertIn("additionalContext", run("usage_prompt_nudge.py", {"session_id": "a"}, self.home))

    def put_since_obs(self, obs_ts, cost_usd, threshold_usd=2.0):
        (self.home / "state").mkdir(parents=True, exist_ok=True)
        (self.home / "state" / "since_obs.json").write_text(json.dumps(
            {"obs_ts": obs_ts, "cost_usd": cost_usd, "requests": 9, "threshold_usd": threshold_usd}), encoding="utf-8")

    def test_spending_since_last_observation_overrides_interval(self):
        now = int(time.time())
        self.put_get_usage(now - 1500, 10)                     # 25분 전, 10% → 60분 간격이 남아 있다
        self.put_since_obs(now - 1500, 1.99)
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "a"}, self.home), "")
        self.put_since_obs(now - 1500, 2.0, threshold_usd=0)   # 기준 0은 끔
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "a"}, self.home), "")
        self.put_since_obs(now - 3000, 9.0)                    # 더 오래된 관측 기준의 값은 쓰지 않는다
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "a"}, self.home), "")
        self.put_since_obs(now - 1500, 2.0)
        self.assertIn("additionalContext", run("usage_prompt_nudge.py", {"session_id": "a"}, self.home))
        self.assertEqual(run("usage_prompt_nudge.py", {"session_id": "b"}, self.home), "")   # 쿨다운 5분
        out = json.loads(run("usage_stop_guard.py", {"session_id": "a"}, self.home))
        self.assertEqual(out["decision"], "block")

    def test_fixed_mode_fallback(self):
        self.put_get_usage(int(time.time()) - 700, 10)
        out = run("usage_prompt_nudge.py", {"session_id": "a"}, self.home, USAGE_MONITOR_INTERVAL_MODE="fixed")
        self.assertIn("additionalContext", out)

    def test_stop_guard(self):
        self.assertEqual(run("usage_stop_guard.py", {"session_id": "a", "stop_hook_active": True}, self.home), "")
        out = json.loads(run("usage_stop_guard.py", {"session_id": "a", "stop_hook_active": False}, self.home))
        self.assertEqual(out["decision"], "block")
        self.assertEqual(run("usage_stop_guard.py", {"session_id": "a"}, self.home), "")

    def test_post_tool_records_plan_and_ctx(self):
        body = {"plan": {"status": "ok", "windows": []}, "context": {"session": "self", "status": "ok",
                                                                     "percentUsed": 26, "categories": [1]}}
        run("usage_post_tool.py", {"session_id": "a", "tool_response": [{"type": "text", "text": json.dumps(body)}]},
            self.home)
        rec = json.loads((self.spool / "usage_tool.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(rec["ctx_pct"], 26)
        self.assertNotIn("categories", json.dumps(rec))

    def test_statusline_writes_spool_and_prints(self):
        stdin = {"session_id": "cli1", "model": {"id": "claude-opus-5[1m]", "display_name": "Opus 5"},
                 "effort": {"level": "high"}, "context_window": {"used_percentage": 7},
                 "rate_limits": {"five_hour": {"used_percentage": 4, "resets_at": 1791366000}},
                 "session_name": "인사 나누기"}
        out = run("statusline.py", stdin, self.home, flags=("-S", "-I"))
        self.assertEqual(out, "Opus 5 | 5h 4% | ctx 7%")
        rec = json.loads((self.spool / "statusline.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(rec["five_hour"], {"pct": 4, "resets_at": 1791366000})
        self.assertIsNone(rec["seven_day"])

    # ── 세션 상태 hook ─────────────────────────────────
    def session_lines(self):
        path = self.spool / "session_events.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def test_session_event_writes_line_without_output(self):
        stdin = {"session_id": "s1", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                 "tool_input": {"command": "rm -rf secret"}, "prompt": "비밀 프롬프트", "cwd": "C:\\PDD"}
        out = run("session_event.py", stdin, self.home)
        self.assertEqual(out, "")
        [rec] = self.session_lines()
        self.assertEqual((rec["event"], rec["session_id"], rec["tool"]), ("PermissionRequest", "s1", "Bash"))
        self.assertNotIn("tool_input", rec)
        self.assertNotIn("prompt", rec)
        self.assertNotIn("secret", json.dumps(rec))
        self.assertIsInstance(rec["ts"], float)

    def test_korean_title_kept(self):
        stdin = {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "session_title": "물류B02 흐름 분석",
                 "cwd": "C:\\PDD\\물류B02", "prompt": "x"}
        run("session_event.py", stdin, self.home)
        [rec] = self.session_lines()
        self.assertEqual((rec["title"], rec["cwd"]), ("물류B02 흐름 분석", "C:\\PDD\\물류B02"))

    def test_session_event_fields_for_notification_and_failure(self):
        run("session_event.py", {"session_id": "s1", "hook_event_name": "Notification",
                                 "notification_type": "permission_prompt", "message": "가" * 300}, self.home)
        run("session_event.py", {"session_id": "s1", "hook_event_name": "StopFailure", "error": "rate_limit",
                                 "last_assistant_message": "API Error"}, self.home)
        note, fail = self.session_lines()
        self.assertEqual((note["ntype"], len(note["message"])), ("permission_prompt", 200))
        self.assertEqual(fail["error"], "rate_limit")
        self.assertNotIn("last_assistant_message", fail)

    def test_session_event_respects_disable(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "disable").write_text("", encoding="utf-8")
        run("session_event.py", {"session_id": "s1", "hook_event_name": "Stop"}, self.home)
        self.assertEqual(self.session_lines(), [])

    def test_session_event_bad_input_exit_zero(self):
        e = dict(os.environ, USAGE_MONITOR_HOME=str(self.home))
        p = subprocess.run([sys.executable, "-S", str(HOOKS / "session_event.py")], input=b"not json",
                           capture_output=True, env=e, timeout=20)
        self.assertEqual((p.returncode, p.stdout), (0, b""))

    def test_session_event_records_for_cli_too(self):
        run("session_event.py", {"session_id": "c1", "hook_event_name": "Stop"}, self.home, entrypoint="cli")
        self.assertEqual(self.session_lines()[0]["session_id"], "c1")


if __name__ == "__main__":
    unittest.main()
