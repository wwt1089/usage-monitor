"""Desktop 세션용 get_usage hook 세 개가 함께 쓰는 경로·판정·기록 함수.

- 관측 기록: ~/.claude/usage-monitor/spool/usage_tool.jsonl (한 줄 = get_usage 결과 하나)
- 다음 관측 시점: 마지막 관측값(위 스풀과 statusline 스풀 중 늦은 쪽)의 사용률로 간격을 정한다.
  간격이 남았어도 그 관측 이후의 추정 비용이 기준을 넘으면 관측한다(수신기가 state/since_obs.json에 써 둔다)
- 쿨다운 마커: ~/.claude/usage-monitor/state/{nudge,block}_at — 모든 세션이 공유
- 끄기: ~/.claude/usage-monitor/disable 파일이 있으면 hook 전체가 아무것도 하지 않는다
"""
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("USAGE_MONITOR_HOME") or Path.home() / ".claude" / "usage-monitor")
SPOOL = ROOT / "spool"
STATE = ROOT / "state"
USAGE_SPOOL = SPOOL / "usage_tool.jsonl"
STATUSLINE_SPOOL = SPOOL / "statusline.jsonl"
SESSION_SPOOL = SPOOL / "session_events.jsonl"     # 세션 상태 hook(session_event.py)이 쓴다
SINCE_OBS = STATE / "since_obs.json"
DISABLE = ROOT / "disable"

# 관측 간격. conditional(기본)은 사용률 구간별 간격, fixed는 운영 중 이상이 있을 때 쓰는 예비안
INTERVAL_MODE = os.environ.get("USAGE_MONITOR_INTERVAL_MODE", "conditional")
FIXED_SEC = int(os.environ.get("USAGE_MONITOR_FIXED_SEC", "600"))
# (5h 기준 %, 7d 기준 %, 간격 초) — 위에서부터 처음 걸리는 구간을 쓴다
TIERS = ((80, 90, 300), (50, 75, 1200), (0, 0, 3600))
# 유도·강제 호출 사이의 최소 간격. 기록 경로가 고장 나도 호출이 이보다 잦아지지 않는다
MIN_COOLDOWN_SEC = 300

TOOL_NAME = "mcp__ccd_session_mgmt__get_usage"


def read_input():
    """hook 입력 JSON. Windows 파이프 stdin은 cp949로 디코딩되므로 바이트로 읽는다."""
    raw = sys.stdin.buffer.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def emit(obj):
    """hook 출력 JSON. 콘솔 인코딩(cp949)을 거치지 않게 UTF-8 바이트로 쓴다."""
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def stale_minutes(age):
    return "한동안" if age > 86400 else f"{int(age // 60)}분째"


def enabled_for_desktop():
    """CLI 세션은 statusline이 한도를 주므로 Desktop 세션에서만 동작한다."""
    if DISABLE.exists():
        return False
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop"


def _iso_to_epoch(s):
    return round(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _limits_of(rec):
    """스풀 한 줄에서 {ts, '5h': (pct, resets_at), '7d': (...)}를 뽑는다. 한도가 없으면 None."""
    out = {}
    if rec.get("source") == "get_usage":
        for w in (rec.get("plan") or {}).get("windows") or []:
            key = {"5-hour limit": "5h", "Weekly · all models": "7d"}.get(w.get("label"))
            if key and w.get("percentUsed") is not None and w.get("resetsAt"):
                out[key] = (w["percentUsed"], _iso_to_epoch(w["resetsAt"]))
    else:
        for src, key in (("five_hour", "5h"), ("seven_day", "7d")):
            w = rec.get(src) or {}
            if w.get("pct") is not None and w.get("resets_at"):
                out[key] = (w["pct"], w["resets_at"])
    if not out:
        return None
    out["ts"] = rec.get("ts", 0)
    return out


def _tail_limits(path, max_bytes=16384):
    """파일 끝에서부터 한도가 들어 있는 마지막 줄을 찾는다."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            tail = f.read()
    except OSError:
        return None
    for line in reversed(tail.split(b"\n")):
        try:
            found = _limits_of(json.loads(line))
        except (ValueError, AttributeError, TypeError):
            continue
        if found:
            return found
    return None


def last_limits():
    found = [x for x in (_tail_limits(USAGE_SPOOL), _tail_limits(STATUSLINE_SPOOL)) if x]
    return max(found, key=lambda x: x["ts"]) if found else None


def observation_interval(limits, now):
    """다음 관측까지의 간격(초). 0이면 지금 관측한다."""
    if INTERVAL_MODE == "fixed":
        return FIXED_SEC
    if limits is None:
        return 0
    if any(limits[k][1] <= now for k in ("5h", "7d") if k in limits):
        return 0  # 창이 리셋돼 마지막 값이 더는 현재 값이 아니다
    p5 = limits.get("5h", (0, 0))[0]
    p7 = limits.get("7d", (0, 0))[0]
    for t5, t7, sec in TIERS:
        if p5 >= t5 or p7 >= t7:
            return sec
    return TIERS[-1][2]


def spent_over_threshold(limits):
    """마지막 관측 이후의 추정 비용이 기준을 넘었는지. 한도 %가 오지 않는 경로(비대화형 CLI 등)로
    많이 쓴 뒤에는 간격을 기다리지 않고 관측하기 위한 판정이다.
    수신기가 쓴 값이 지금 아는 관측보다 오래된 관측 기준이면(수신기가 꺼져 있거나 아직 못 읽었다) 쓰지 않는다."""
    if not limits:
        return False
    try:
        rec = json.loads(SINCE_OBS.read_text(encoding="utf-8"))
        return rec["obs_ts"] >= limits["ts"] - 1 and 0 < rec["threshold_usd"] <= rec["cost_usd"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def due(now):
    """관측할 때가 됐는지와 마지막 관측 이후 경과 초, 쿨다운 초."""
    limits = last_limits()
    interval = observation_interval(limits, now)
    age = now - limits["ts"] if limits else float("inf")
    if age < interval and spent_over_threshold(limits):
        return True, age, MIN_COOLDOWN_SEC
    return age >= interval, age, max(interval, MIN_COOLDOWN_SEC)


def marker_age(name, now):
    try:
        return now - (STATE / name).stat().st_mtime
    except OSError:
        return float("inf")


def touch_marker(name, session_id):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(session_id or "", encoding="utf-8")


def append_line(path, record):
    """한 줄을 os.write 한 번으로 append한다. 도중에 취소돼도 줄이 섞이지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def debug_dump(event, payload):
    """검증 단계용. 이벤트별 마지막 입력과 환경 일부를 덮어쓴다."""
    STATE.mkdir(parents=True, exist_ok=True)
    payload = dict(payload, _entrypoint=os.environ.get("CLAUDE_CODE_ENTRYPOINT"), _ts=time.time())
    (STATE / f"debug_{event}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def host_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


NUDGE_TEXT = (
    "[usage-monitor] 한도 관측이 {age} 갱신되지 않았습니다. "
    f"이번 턴의 다른 도구 호출과 함께 {TOOL_NAME} 를 한 번 호출합니다. "
    f"도구가 로드돼 있지 않으면 ToolSearch 로 select:{TOOL_NAME} 를 먼저 불러옵니다. "
    "결과는 hook이 기록하므로 응답에서 언급하지 않습니다."
)

BLOCK_TEXT = (
    "[usage-monitor] 한도 관측이 {age} 갱신되지 않았습니다. "
    f"{TOOL_NAME} 를 한 번 호출하고 추가 설명 없이 턴을 마칩니다. "
    f"도구가 로드돼 있지 않으면 ToolSearch 로 select:{TOOL_NAME} 를 먼저 불러옵니다."
)
