"""트레이 알림 로직. pystray·창과 떨어진 순수 로직이라 단독으로 시험한다.

- parse_sse: 수신기 /stream 의 줄 → (이벤트, 데이터)
- badge · tooltip_part: 트레이 아이콘 배지와 툴팁 문구
- AlertTracker: 확인 안 한 대기·결정 필요가 새로 생기면 숨긴 위젯을 띄우라고 알린다
  (세션 Windows 알림은 사용자 요청으로 뺐다. 배지·위젯·화면 강조로 충분하다)
"""
import json

ALERT_STATES = ("waiting", "decision", "error")             # 배지(주황)에 세는 상태
WIDGET_STATES = ("waiting", "decision")                     # 숨긴 위젯을 띄우는 상태
TOOLTIP_ORDER = (("waiting", "대기"), ("decision", "결정"), ("error", "오류"), ("done", "완료"),
                 ("working", "작업 중"))


def parse_sse(lines):
    """SSE 줄 묶음에서 (event, data dict)를 낸다. data가 JSON이 아니면 건너뛴다."""
    event, data = None, []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            # 빈 줄이 메시지의 끝
            if event and data:
                try:
                    yield event, json.loads("\n".join(data))
                except ValueError:
                    pass
            event, data = None, []


def badge(live):
    """('alert', 개수) | ('done', '') | (None, ''). 확인 안 한 세션만 센다."""
    unseen = (live or {}).get("unseen") or {}
    n = sum(unseen.get(st, 0) for st in ALERT_STATES)
    if n:
        return "alert", "9+" if n > 9 else str(n)
    if unseen.get("done"):
        return "done", ""
    return None, ""


def tooltip_part(live):
    """'대기 2 · 오류 1 · 완료 3' — 0인 상태는 뺀다."""
    counts = (live or {}).get("counts") or {}
    return " · ".join(f"{label} {counts[st]}" for st, label in TOOLTIP_ORDER if counts.get(st))


def _key(item):
    return item["session_id"], item["state"], item["state_ts"]


class AlertTracker:
    """스냅샷이 올 때마다 새로 생긴 상태를 보고 숨긴 위젯을 띄울지 정한다(Windows 알림은 내지 않는다)."""

    def __init__(self):
        self.started = False
        self.known = set()            # 이미 본 (세션, 상태, 상태 시각)

    def update(self, items, now=None):
        """확인 안 한 대기·결정 필요가 새로 생겼으면 True. 처음 받은 스냅샷(트레이 시작 직후)은 기억만 한다."""
        keys = {_key(i): i for i in items}
        if not self.started:
            self.started = True
            self.known = set(keys)
            return False
        wake = any(k not in self.known and not i["seen"] and i["state"] in WIDGET_STATES for k, i in keys.items())
        self.known = set(keys)        # 지금 보이는 것만 기억한다(오래 켜 둬도 늘지 않게)
        return wake
