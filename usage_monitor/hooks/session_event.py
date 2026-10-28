"""세션 상태 hook. UserPromptSubmit · PermissionRequest · Notification · PostToolUse(Failure) · Stop(Failure) · SessionStart

입력에서 화면에 필요한 값만 골라 ~/.claude/usage-monitor/spool/session_events.jsonl 에 한 줄로 적는다.
- 프롬프트·도구 입력·응답 본문은 적지 않는다
- 아무것도 출력하지 않는다(PermissionRequest·UserPromptSubmit의 출력은 Claude 동작을 바꾸므로)
- 어떤 오류가 나도 조용히 끝낸다(세션 진행을 막지 않는다)
- CLI·Desktop 세션 모두 기록한다. disable 파일이 있으면 아무것도 하지 않는다
"""
import time

try:                                   # 실행 파일(um-hook.exe) 안에서는 패키지 모듈로 불린다
    from . import usage_hook_common as c
except ImportError:                    # Python 스크립트로 직접 실행될 때
    import usage_hook_common as c

MESSAGE_MAX = 200


def _text(value, limit=None):
    """문자열로 바꾸고 길이를 자른다. 빈 값은 None."""
    if value is None or value == "":
        return None
    s = value if isinstance(value, str) else str(value)
    return s[:limit] if limit else s


def record(inp, now=None):
    """hook 입력 → 스풀 한 줄."""
    return {
        "ts": round(now if now is not None else time.time(), 3),
        "event": inp.get("hook_event_name"),
        "session_id": inp.get("session_id"),
        "title": _text(inp.get("session_title")),
        "cwd": _text(inp.get("cwd")),
        "tool": _text(inp.get("tool_name")),
        "ntype": _text(inp.get("notification_type")),
        "message": _text(inp.get("message"), MESSAGE_MAX),
        "error": _text(inp.get("error")),          # StopFailure의 오류 종류(rate_limit 등)
        "agent_id": _text(inp.get("agent_id")),
        "host": c.host_name(),
    }


def main():
    # 1단계: 끄기 파일이 있으면 아무것도 하지 않는다
    if c.DISABLE.exists():
        return
    # 2단계: 입력을 읽고, 세션 ID가 없으면 버린다
    inp = c.read_input()
    if not inp.get("session_id") or not inp.get("hook_event_name"):
        return
    # 3단계: 한 줄로 덧붙인다
    c.append_line(c.SESSION_SPOOL, record(inp))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                           # 기록 실패는 세션에 영향을 주지 않는다
