"""가짜 데이터 주입. 실제 세션 없이 화면을 확인할 때 쓴다.

실제 스풀·DB와 섞이지 않게 별도 데이터 폴더와 포트로 수신기를 띄운 뒤 실행한다.

    set USAGE_MONITOR_HOME=%TEMP%\\usage-monitor-fake
    set USAGE_MONITOR_PORT=4319
    python -m usage_monitor receiver
    python scripts/fake_feed.py --url http://127.0.0.1:4319 --home %TEMP%\\usage-monitor-fake

- OTLP: 2~6초마다 api_request 하나를 /v1/logs로 보낸다
- 한도: 30초마다 statusline 스풀에 한 줄 쓴다. 5h는 --start-pct에서 시간당 --rate %p씩 오른다
- --sessions: 세션 상태 시나리오(8개: 대기 2, 완료 3, 작업 중 2, 오류 1)를 세션 스풀에 한 번 쓰고 끝낸다
  --sessions --add-waiting: 새 승인 대기 세션 하나만 덧붙인다(토스트·위젯 띄우기 확인용)
"""
import argparse
import json
import random
import time
import urllib.request
import uuid
from pathlib import Path

DEFAULT_HOME = Path.home() / ".claude" / "usage-monitor"


def av(v):
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": v}


def otlp_request(session, host):
    out = random.randint(50, 2500)
    cache_read = random.randint(20_000, 120_000)
    cache_creation = random.choice([0, 0, random.randint(500, 20_000)])
    micros = int(cache_read * 0.25 + cache_creation * 3.125 + out * 12.5)
    attrs = {"session.id": session, "event.name": "api_request", "model": "claude-opus-5",
             "input_tokens": random.randint(1, 900), "output_tokens": out, "cache_read_tokens": cache_read,
             "cache_creation_tokens": cache_creation, "cost_usd_micros": micros, "cost_usd": micros / 1e6,
             "duration_ms": random.randint(800, 9000), "request_id": "req_fake_" + uuid.uuid4().hex[:16],
             "speed": "normal", "query_source": "repl_main_thread", "effort": "high"}
    ns = str(time.time_ns())
    return {"resourceLogs": [{"resource": {"attributes": [
        {"key": "service.name", "value": av("claude-code")}, {"key": "host.name", "value": av(host)}]},
        "scopeLogs": [{"logRecords": [{"timeUnixNano": ns, "body": av("claude_code.api_request"),
                                       "attributes": [{"key": k, "value": av(v)} for k, v in attrs.items()]}]}]}]}


def session_scenario(now):
    """세션 8개. 6줄을 넘겨 '외 N개'와 한글 제목, 오래된 작업 중(회색)을 함께 본다."""
    ev = lambda sid, kind, ago, **kw: dict({"ts": round(now - ago, 3), "event": kind, "session_id": sid,
                                            "host": "FAKE-PC"}, **kw)
    return [
        ev("f-wait1", "UserPromptSubmit", 300, title="물류B02 흐름 분석", cwd="C:/PDD/물류B02"),
        ev("f-wait1", "Notification", 120, ntype="permission_prompt",
           message="Claude needs your permission to use Bash"),
        ev("f-wait2", "UserPromptSubmit", 200, title="견적 OCR 개선", cwd="C:/PDD/HW_Agent"),
        ev("f-wait2", "Notification", 60, ntype="permission_prompt",
           message="Claude needs your permission to use AskUserQuestion"),
        ev("f-done1", "UserPromptSubmit", 600, title="Node.js 설치 검토"),
        ev("f-done1", "Stop", 240),
        ev("f-done2", "UserPromptSubmit", 1200, title="Questi:ON 링크 정리"),
        ev("f-done2", "Stop", 900),
        ev("f-done3", "UserPromptSubmit", 1800, title="트렌드리포트 발송 오류 분석"),
        ev("f-done3", "Stop", 1500),
        ev("f-work", "UserPromptSubmit", 30, title="사용량 모니터 개선", cwd="C:/PDD/usage-monitor-main"),
        ev("f-stale", "UserPromptSubmit", 2500, title="응답 없는 세션"),
        ev("f-err", "UserPromptSubmit", 500, title="PPT 매뉴얼 수정"),
        ev("f-err", "StopFailure", 400, error="rate_limit"),
    ]


def write_sessions(home, add_waiting):
    """세션 스풀에 시나리오(또는 새 대기 하나)를 덧붙인다."""
    path = home / "spool" / "session_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if add_waiting:
        sid = "f-new-" + uuid.uuid4().hex[:4]
        lines = [{"ts": round(now, 3), "event": "Notification", "session_id": sid, "host": "FAKE-PC",
                  "title": "새로 승인 기다리는 세션", "ntype": "permission_prompt",
                  "message": "Claude needs your permission to use Edit"}]
    else:
        lines = session_scenario(now)
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(f"세션 이벤트 {len(lines)}줄 → {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:4319")
    ap.add_argument("--home", required=True, help="수신기의 USAGE_MONITOR_HOME과 같은 폴더")
    ap.add_argument("--start-pct", type=float, default=35)
    ap.add_argument("--rate", type=float, default=15, help="5h 사용률 증가 속도(%%p/시간)")
    ap.add_argument("--host", default="FAKE-PC")
    ap.add_argument("--sessions", action="store_true", help="세션 상태 시나리오를 쓰고 끝낸다")
    ap.add_argument("--add-waiting", action="store_true", help="--sessions와 함께: 새 대기 세션 하나만")
    args = ap.parse_args()

    home = Path(args.home)
    if home.resolve() == DEFAULT_HOME.resolve():
        raise SystemExit("실제 데이터 폴더에는 쓰지 않는다. --home에 별도 폴더를 준다")
    if args.sessions:
        write_sessions(home, args.add_waiting)
        return
    spool = home / "spool" / "statusline.jsonl"
    spool.parent.mkdir(parents=True, exist_ok=True)

    session = "fake-" + uuid.uuid4().hex[:8]
    t0 = time.time()
    r5 = (int(t0) // 3600 + 3) * 3600        # 리셋은 2~3시간 뒤 정각
    r7 = r5 + 3 * 86400
    next_limit = 0.0
    print(f"session={session} → {args.url}, spool={spool}")
    while True:
        now = time.time()
        req = urllib.request.Request(args.url + "/v1/logs", data=json.dumps(otlp_request(session, args.host)).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
        except OSError as e:
            print("OTLP 전송 실패:", e)
        if now >= next_limit:
            pct5 = min(100, int(args.start_pct + args.rate * (now - t0) / 3600))
            line = {"ts": int(now), "host": args.host, "session_id": session, "model": "Opus 5",
                    "model_id": "claude-opus-5[1m]", "effort": "high",
                    "five_hour": {"pct": pct5, "resets_at": r5}, "seven_day": {"pct": 41, "resets_at": r7},
                    "ctx_pct": random.randint(5, 60), "cache_hit": round(random.uniform(0.6, 0.98), 3),
                    "cache_warm": True, "cost_usd": None}
            with open(spool, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            print(time.strftime("%H:%M:%S"), f"5h {pct5}%")
            next_limit = now + 30
        time.sleep(random.uniform(2, 6))


if __name__ == "__main__":
    main()
