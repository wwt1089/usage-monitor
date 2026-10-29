"""세션 상태 전이 규칙. DB·시계에 기대지 않는 순수 함수만 둔다.

상태: working(작업 중) · waiting(승인·응답 대기, Notification으로만 들어감) · decision(결정 필요) · done(완료) · error(오류)
- apply_event: hook 스풀 한 줄을 반영한다
- apply_meta: 앱 메타데이터(appmeta.parse_meta 결과)로 "결정 필요"와 "열어 봄"을 보강한다
- 모든 함수는 입력 행을 바꾸지 않고 새 dict를 돌려준다
시각은 epoch 초. 메타데이터의 밀리초 값은 여기서 1000으로 나눈다.
"""

STATES = ("working", "waiting", "decision", "done", "error")
# 목록 순서: 내 응답이 필요한 것 → 진행 중 → 완료(아래로)
PRIORITY = {"waiting": 0, "decision": 1, "error": 2, "working": 3, "done": 4}
WAITING_NTYPES = frozenset({"permission_prompt", "elicitation_dialog", "elicitation_url_dialog",
                            "agent_needs_input"})
ATTENTION = frozenset({"waiting", "decision", "done", "error"})   # 바뀌면 "확인 안 함"이 되는 상태
VISIBLE_SEC = 7200          # 확인한 세션을 목록에 남겨 두는 시간
STALE_SEC = 1800            # 작업 중인데 이벤트가 이만큼 없으면 회색으로 표시
DETAIL_MAX = 80
STOP_JITTER_SEC = 0.5      # Stop 직후 도구 이벤트를 '이어진 턴'으로 보지 않는 여유
EDGE_TOLERANCE_SEC = 2      # 메타데이터 턴 경계와 Stop 시각의 허용 오차(실측 0.2초). 넓으면 이전 턴 요약을 새 턴으로 착각한다


def _cut(text):
    """detail은 80자까지만 남긴다. 빈 값은 None."""
    return str(text)[:DETAIL_MAX] if text else None


PERMISSION_PREFIX = "Claude needs your permission to use "


def _notification_detail(message):
    """'Claude needs your permission to use X' 는 도구 이름 X만 남긴다. 다른 문구는 그대로."""
    if message and message.startswith(PERMISSION_PREFIX):
        return _cut(message[len(PERMISSION_PREFIX):].strip())
    return _cut(message)


def _new_row(ev):
    """처음 보는 세션. 조용한 완료 상태로 시작한다(SessionStart만 왔을 때 강조하지 않도록)."""
    return {"session_id": ev["session_id"], "app_session_id": None, "host": None, "title": None, "cwd": None,
            "state": "done", "detail": None, "state_ts": ev["ts"], "last_event_ts": ev["ts"],
            "stop_ts": None, "seen": 1, "archived": 0}


def _transition(row, ev):
    """이벤트가 정하는 (다음 상태, detail, 새 에피소드 여부). 전이가 없으면 None."""
    kind = ev.get("event")
    if kind == "UserPromptSubmit":
        return "working", None, True       # 새 턴: 이전 턴의 늦은 Stop이 덮지 못하게 기준 시각을 새로 잡는다
    # PermissionRequest는 auto 모드 분류기가 판단할 때도 와서(승인 창 없음) 대기로 보지 않는다.
    # 실제로 승인 창이 6초 넘게 떠 있으면 Notification(permission_prompt)이 온다(2026-10-28 실측)
    if kind == "Notification" and ev.get("ntype") in WAITING_NTYPES:
        return "waiting", _notification_detail(ev.get("message")), False
    if kind in ("PostToolUse", "PostToolUseFailure") and row["state"] == "waiting":
        return "working", None, False
    # Stop 뒤에 도구가 또 돌면 턴이 이어진 것이다(Stop hook이 decision: block으로 종료를 막은 경우).
    # Stop 직후 0.5초 안의 도구 이벤트는 마지막 도구의 늦은 기록일 수 있어 무시한다
    if (kind in ("PostToolUse", "PostToolUseFailure") and row["state"] in ("done", "decision")
            and row.get("stop_ts") and ev["ts"] > row["stop_ts"] + STOP_JITTER_SEC):
        return "working", None, False
    if kind == "Stop":
        return "done", None, True          # 턴이 끝날 때마다 새 완료로 친다
    if kind == "StopFailure":
        return "error", _cut(ev.get("error")), True
    return None


def apply_event(row, ev):
    """스풀 한 줄을 반영한 새 행을 돌려준다. SessionStart만 온 새 세션이면 None(목록에 올리지 않음)."""
    # 0단계: SessionStart는 앱이 세션을 복원할 때마다 온다. 제목·폴더만 갱신하고 활동 시각은 건드리지 않는다
    #        (그러지 않으면 어제 끝난 세션이 앱 재시작 때마다 목록에 다시 나타난다)
    if ev.get("event") == "SessionStart":
        if not row:
            return None
        row = dict(row)
        if ev.get("title") and not row.get("app_session_id"):
            row["title"] = ev["title"]
        for key in ("cwd", "host"):
            if ev.get(key):
                row[key] = ev[key]
        return row
    # 1단계: 행이 없으면 새로 만들고, 있으면 복사본에서 작업한다
    row = dict(row) if row else _new_row(ev)

    # 2단계: 제목·폴더·호스트는 순서와 관계없이 갱신한다(앱 제목이 있으면 hook 제목으로 덮지 않음)
    if ev.get("title") and not row.get("app_session_id"):
        row["title"] = ev["title"]
    for key in ("cwd", "host"):
        if ev.get(key):
            row[key] = ev[key]

    # 3단계: 지금 상태가 시작된 시각보다 이른 이벤트는 상태를 바꾸지 않는다(async hook 순서 뒤집힘 대비)
    #   기준을 last_event_ts로 잡으면 상태를 바꾸지 않은 이벤트(작업 중의 PostToolUse)가 기준을 올려,
    #   hook 기동 지연으로 수십 ms 앞선 PermissionRequest·Stop을 버리게 된다
    ts = ev["ts"]
    row["last_event_ts"] = max(row["last_event_ts"], ts)
    if ts < row["state_ts"]:
        return row

    # 4단계: 상태 전이
    step = _transition(row, ev)
    if step is None:
        return row
    state, detail, new_episode = step
    if state != row["state"] or new_episode:
        row["state_ts"] = ts
        if state in ATTENTION:
            row["seen"] = 0
    row["state"], row["detail"] = state, detail
    if ev.get("event") == "Stop":
        row["stop_ts"] = ts
    if ev.get("event") == "UserPromptSubmit":
        row["seen"] = 1                    # 지시를 보냈다면 이미 본 것이다
    return row


def apply_meta(row, meta, now=None):
    """앱 메타데이터로 보강한 새 행을 돌려준다. now는 '결정 필요'로 바뀐 시각(없으면 Stop 시각)."""
    row = dict(row)

    # 1단계: 앱 쪽 식별자·제목·보관 여부
    row["app_session_id"] = meta.get("app_session_id") or row.get("app_session_id")
    if meta.get("title"):
        row["title"] = meta["title"]
    row["archived"] = 1 if meta.get("archived") else 0

    # 2단계: 완료 직후 앱 요약이 "blocked"면 결정 필요로 바꾼다(최신 요약이고 이번 턴의 것일 때만)
    if (row["state"] == "done" and row.get("stop_ts") and meta.get("summary_fresh")
            and meta.get("status_category") == "blocked"
            and (meta.get("last_turn_edge_ms") or 0) / 1000 >= row["stop_ts"] - EDGE_TOLERANCE_SEC):
        row["state"] = "decision"
        row["detail"] = _cut(meta.get("needs_action"))
        # 완료를 먼저 봤더라도 할 일이 새로 생긴 것이므로 다시 '확인 안 함'으로. 그 뒤에 열어 봐야 확인함
        row["seen"] = 0
        row["state_ts"] = now if now is not None else row["state_ts"]

    # 3단계: 상태가 바뀐 뒤에 앱에서 그 세션을 열었으면 확인한 것으로 본다
    if row["seen"] == 0 and (meta.get("last_focused_ms") or 0) / 1000 > row["state_ts"]:
        row["seen"] = 1
    return row


def is_visible(row, now):
    """목록에 보일지. 보관된 세션은 숨기고, 확인 안 한 세션은 시간이 지나도 남긴다."""
    if row.get("archived"):
        return False
    return row["seen"] == 0 or now - row["last_event_ts"] <= VISIBLE_SEC


def is_stale(row, now):
    """작업 중인데 오래 소식이 없는지(앱 강제 종료 등)."""
    return row["state"] == "working" and now - row["last_event_ts"] > STALE_SEC


def sort_key(row):
    """상태 우선순위(대기·결정·오류 → 작업 중 → 완료) → 확인 안 함 → 최근 순."""
    return (PRIORITY.get(row["state"], 9), row["seen"], -row["last_event_ts"])
