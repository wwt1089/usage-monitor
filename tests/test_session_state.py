"""세션 상태 전이 규칙(session_state.py) 표 테스트."""
import unittest

from usage_monitor import session_state as ss


def ev(event, ts, sid="s1", **kw):
    """스풀 한 줄과 같은 모양의 이벤트를 만든다."""
    base = {"ts": ts, "event": event, "session_id": sid, "title": None, "cwd": None, "tool": None,
            "ntype": None, "message": None, "error": None, "agent_id": None, "host": "PC1"}
    base.update(kw)
    return base


def run(*events, row=None):
    """이벤트를 차례로 적용한 마지막 행을 돌려준다."""
    for e in events:
        row = ss.apply_event(row, e)
    return row


def meta(**kw):
    """parse_meta 결과와 같은 모양의 메타데이터."""
    base = {"cli_session_id": "s1", "app_session_id": "local_x", "title": None, "status_category": None,
            "needs_action": None, "summary_fresh": False, "last_turn_edge_ms": 0, "last_focused_ms": 0,
            "archived": False}
    base.update(kw)
    return base


class ApplyEventTest(unittest.TestCase):
    def test_session_start_alone_makes_no_row(self):
        # 앱이 세션을 복원할 때(resume)마다 오므로, 이것만으로는 목록에 올리지 않는다
        self.assertIsNone(ss.apply_event(None, ev("SessionStart", 10, title="물류B02")))

    def test_session_start_does_not_revive_old_session(self):
        row = run(ev("UserPromptSubmit", 10), ev("Stop", 20))
        row = dict(row, seen=1)
        row = ss.apply_event(row, ev("SessionStart", 5000, title="새 제목"))
        self.assertEqual((row["last_event_ts"], row["state_ts"], row["title"]), (20, 20, "새 제목"))

    def test_permission_then_tool_resumes_then_stop(self):
        row = run(ev("UserPromptSubmit", 10), ev("Notification", 20, ntype="permission_prompt", message="Claude needs your permission to use Bash"))
        self.assertEqual((row["state"], row["detail"], row["seen"], row["state_ts"]), ("waiting", "Bash", 0, 20))
        row = run(ev("PostToolUse", 30), row=row)
        self.assertEqual((row["state"], row["state_ts"], row["detail"]), ("working", 30, None))
        row = run(ev("Stop", 40), row=row)
        self.assertEqual((row["state"], row["stop_ts"], row["seen"]), ("done", 40, 0))

    def test_question_notification_waits_other_types_ignored(self):
        msg = "Claude needs your permission to use AskUserQuestion"
        row = run(ev("UserPromptSubmit", 10), ev("Notification", 20, ntype="permission_prompt", message=msg))
        self.assertEqual((row["state"], row["detail"]), ("waiting", "AskUserQuestion"))   # 도구 이름만 남긴다
        row = run(ev("Notification", 20, ntype="elicitation_dialog", message="서버가 값을 묻습니다"))
        self.assertEqual(row["detail"], "서버가 값을 묻습니다")                           # 형식이 다르면 그대로
        row = run(ev("UserPromptSubmit", 10), ev("Notification", 20, ntype="auth_success", message="x"))
        self.assertEqual(row["state"], "working")

    def test_post_tool_does_not_wake_done(self):
        row = run(ev("UserPromptSubmit", 10), ev("Stop", 40), ev("PostToolUse", 41))
        self.assertEqual(row["state"], "done")

    def test_out_of_order_event_updates_title_only(self):
        row = run(ev("UserPromptSubmit", 50))
        row = run(ev("Notification", 45, title="새 제목", ntype="permission_prompt", message="Claude needs your permission to use Bash"), row=row)
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["title"], "새 제목")
        self.assertEqual(row["last_event_ts"], 50)

    def test_stop_failure_sets_error_detail(self):
        row = run(ev("UserPromptSubmit", 10), ev("StopFailure", 20, error="rate_limit"))
        self.assertEqual((row["state"], row["detail"], row["seen"]), ("error", "rate_limit", 0))

    def test_prompt_marks_seen(self):
        row = run(ev("UserPromptSubmit", 10), ev("Stop", 20))
        self.assertEqual(row["seen"], 0)
        row = run(ev("UserPromptSubmit", 30), row=row)
        self.assertEqual((row["state"], row["seen"]), ("working", 1))

    def test_detail_truncated_to_80(self):
        row = run(ev("Notification", 10, ntype="permission_prompt", message="가" * 200))
        self.assertEqual(len(row["detail"]), 80)

    def test_same_state_keeps_state_ts_but_updates_detail(self):
        row = run(ev("Notification", 10, ntype="permission_prompt", message="Claude needs your permission to use Bash"), ev("Notification", 20, ntype="permission_prompt", message="Claude needs your permission to use Edit"))
        self.assertEqual((row["state_ts"], row["detail"], row["last_event_ts"]), (10, "Edit", 20))

    def test_input_row_not_mutated(self):
        row = run(ev("UserPromptSubmit", 10))
        before = dict(row)
        ss.apply_event(row, ev("Stop", 20))
        self.assertEqual(row, before)


class ApplyMetaTest(unittest.TestCase):
    def done_row(self):
        return run(ev("UserPromptSubmit", 90), ev("Stop", 100))

    def test_meta_blocked_turns_done_into_decision(self):
        row = ss.apply_meta(self.done_row(), meta(summary_fresh=True, status_category="blocked",
                                                  needs_action="Node.js 설치 승인", last_turn_edge_ms=99_500))
        self.assertEqual((row["state"], row["detail"], row["seen"], row["state_ts"]),
                         ("decision", "Node.js 설치 승인", 0, 100))
        self.assertEqual(row["app_session_id"], "local_x")

    def test_meta_stale_summary_ignored(self):
        row = ss.apply_meta(self.done_row(), meta(summary_fresh=False, status_category="blocked",
                                                  last_turn_edge_ms=99_500))
        self.assertEqual(row["state"], "done")
        row = ss.apply_meta(self.done_row(), meta(summary_fresh=True, status_category="blocked",
                                                  last_turn_edge_ms=90_000))
        self.assertEqual(row["state"], "done")

    def test_meta_completed_stays_done(self):
        row = ss.apply_meta(self.done_row(), meta(summary_fresh=True, status_category="review_ready",
                                                  last_turn_edge_ms=100_000))
        self.assertEqual(row["state"], "done")

    def test_meta_focus_marks_seen(self):
        self.assertEqual(ss.apply_meta(self.done_row(), meta(last_focused_ms=101_000))["seen"], 1)
        self.assertEqual(ss.apply_meta(self.done_row(), meta(last_focused_ms=99_000))["seen"], 0)

    def test_meta_title_wins_over_hook_title(self):
        row = ss.apply_meta(self.done_row(), meta(title="앱 제목"))
        row = ss.apply_event(row, ev("UserPromptSubmit", 200, title="hook 제목"))
        self.assertEqual(row["title"], "앱 제목")

    def test_meta_archived(self):
        self.assertEqual(ss.apply_meta(self.done_row(), meta(archived=True))["archived"], 1)


class VisibilityTest(unittest.TestCase):
    def test_visibility_and_stale(self):
        now = 10_000
        row = run(ev("UserPromptSubmit", now - 7201))
        self.assertFalse(ss.is_visible(row, now))                 # 확인함, 2시간 넘음
        row2 = run(ev("Stop", now - 7201), row=dict(row, last_event_ts=0))
        self.assertTrue(ss.is_visible(row2, now))                 # 확인 안 함은 계속 보인다
        self.assertFalse(ss.is_visible(dict(row2, archived=1), now))
        self.assertTrue(ss.is_stale(run(ev("UserPromptSubmit", now - 1801)), now))
        self.assertFalse(ss.is_stale(run(ev("UserPromptSubmit", now - 100)), now))

    def test_sort_waiting_and_working_above_done(self):
        # 사용자 요청(10/28): 응답 대기·진행 중은 위로, 완료는 아래로. 같은 상태 안에서는 확인 안 함 → 최근 순
        unseen_done = run(ev("Stop", 30, sid="a"))
        unseen_wait = run(ev("Notification", 10, sid="b", ntype="permission_prompt", message="Claude needs your permission to use Bash"))
        seen_wait = dict(run(ev("Notification", 50, sid="c", ntype="permission_prompt", message="Claude needs your permission to use Bash")), seen=1)
        working = run(ev("UserPromptSubmit", 5, sid="w"))
        decision = dict(run(ev("Stop", 20, sid="d")), state="decision")
        rows = sorted([seen_wait, unseen_done, working, decision, unseen_wait], key=ss.sort_key)
        self.assertEqual([r["session_id"] for r in rows], ["b", "c", "d", "w", "a"])


if __name__ == "__main__":
    unittest.main()


class JitterTest(unittest.TestCase):
    """hook 시각은 Python 기동 지연만큼 흔들린다. 상태를 바꾸지 않은 이벤트가 순서 기준을 올려선 안 된다."""

    def test_parallel_post_tool_before_permission_still_waits(self):
        row = run(ev("UserPromptSubmit", 10), ev("PostToolUse", 20.5), ev("Notification", 20.4, ntype="permission_prompt", message="Claude needs your permission to use Bash"))
        self.assertEqual((row["state"], row["detail"]), ("waiting", "Bash"))
        self.assertEqual(row["last_event_ts"], 20.5)

    def test_stop_slightly_before_last_post_tool_still_done(self):
        row = run(ev("UserPromptSubmit", 10), ev("PostToolUse", 30.2), ev("Stop", 30.1))
        self.assertEqual(row["state"], "done")


class AutoModeTest(unittest.TestCase):
    """auto 모드는 승인 창 없이도 PermissionRequest를 낸다(분류기 판단). 이것만으로는 대기로 보지 않는다."""

    def test_permission_request_alone_does_not_wait(self):
        row = run(ev("UserPromptSubmit", 10), ev("PermissionRequest", 20, tool="Bash"))
        self.assertEqual((row["state"], row["seen"]), ("working", 1))


class NewTurnTest(unittest.TestCase):
    """새 지시(UserPromptSubmit)는 같은 '작업 중'이어도 새 기준 시각을 잡는다(Codex 리뷰)."""

    def test_late_stop_of_previous_turn_ignored(self):
        row = run(ev("UserPromptSubmit", 100), ev("UserPromptSubmit", 200), ev("Stop", 150))
        self.assertEqual((row["state"], row["state_ts"]), ("working", 200))


class DecisionReviewTest(unittest.TestCase):
    """원작자 리뷰: 결정 필요로 바뀌면 다시 '확인 안 함', 이전 턴 요약이 새 턴을 결정 필요로 만들지 않게."""

    def done_seen(self):
        return dict(run(ev("UserPromptSubmit", 90), ev("Stop", 100)), seen=1)

    def test_decision_resets_seen_and_uses_now(self):
        row = ss.apply_meta(self.done_seen(), meta(summary_fresh=True, status_category="blocked",
                                                   last_turn_edge_ms=99_900, last_focused_ms=100_500), now=105)
        self.assertEqual((row["state"], row["seen"], row["state_ts"]), ("decision", 0, 105))

    def test_previous_turn_summary_ignored(self):
        # 이전 턴 경계(96초)가 새 턴의 Stop(100초)보다 2초 넘게 앞서면 그 요약은 이번 턴 것이 아니다
        row = ss.apply_meta(self.done_seen(), meta(summary_fresh=True, status_category="blocked",
                                                   last_turn_edge_ms=96_000), now=105)
        self.assertEqual(row["state"], "done")
