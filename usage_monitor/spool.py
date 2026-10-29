"""스풀 JSONL tail. statusline.jsonl(CLI), usage_tool.jsonl(Desktop get_usage hook),
session_events.jsonl(세션 상태 hook)을 읽는다.

- 바이트 오프셋을 DB(spool_offsets)에 저장해 재시작 후 이어 읽는다
- 파일이 바뀌었거나(st_ino 변경) 잘렸으면(크기 < 오프셋) 처음부터 읽는다
- 개행으로 끝난 줄만 처리한다. 끝의 미완성 줄은 다음 poll까지 남겨 둔다
"""
import json
import logging
import math
import os
from datetime import datetime

from . import db, session_state

log = logging.getLogger(__name__)

WINDOW_LABELS = {"5-hour limit": "5h", "Weekly · all models": "7d"}


def round_minute(epoch_s):
    """두 소스의 리셋 시각을 맞춘다. statusline은 정각 정수, get_usage는 ±0.3초 ISO로 온다."""
    return int(round(epoch_s / 60)) * 60


def window_key(label):
    if label in WINDOW_LABELS:
        return WINDOW_LABELS[label]
    if label and label.startswith("Weekly · "):
        return "7d:" + label.removeprefix("Weekly · ").strip().lower()
    return None


def _iso_epoch(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def ingest_statusline(conn, rec, default_host):
    ts, sid = int(rec["ts"]), rec.get("session_id")
    host = rec.get("host") or default_host
    for key, win in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rec.get(key)
        if w and w.get("pct") is not None and w.get("resets_at"):
            db.insert_observation(conn, ts, host, sid, "statusline", win, w["pct"], round_minute(w["resets_at"]))
    warm = rec.get("cache_warm")
    db.upsert_session(
        conn, sid, ts, host=host, client="claude-code", model=rec.get("model"), effort=rec.get("effort"),
        ctx_pct=rec.get("ctx_pct"), cache_hit=rec.get("cache_hit"),
        cache_warm=None if warm is None else int(bool(warm)),
    )
    db.touch_health(conn, host, "statusline", ts)


def ingest_get_usage(conn, rec, default_host):
    ts, sid = int(rec["ts"]), rec.get("session_id")
    host = rec.get("host") or default_host
    db.touch_health(conn, host, "get_usage", ts)
    db.upsert_session(conn, sid, ts, host=host, client="claude-code-desktop", ctx_pct=rec.get("ctx_pct"))
    plan = rec.get("plan") or {}
    if plan.get("status") != "ok":
        return
    for w in plan.get("windows") or []:
        win = window_key(w.get("label"))
        if win and w.get("percentUsed") is not None and w.get("resetsAt"):
            db.insert_observation(conn, ts, host, sid, "get_usage", win, w["percentUsed"],
                                  round_minute(_iso_epoch(w["resetsAt"])))


class SpoolTailer:
    def __init__(self, conn, path, handler, default_host):
        self.conn, self.path, self.handler, self.default_host = conn, path, handler, default_host

    def poll(self):
        """새 완성 줄을 처리하고 처리한 줄 수를 돌려준다."""
        try:
            st = self.path.stat()
        except OSError:
            return 0
        file_id = str(st.st_ino)
        saved_id, offset = db.get_offset(self.conn, self.path)
        if saved_id != file_id or st.st_size < offset:
            offset = 0
        if st.st_size == offset:
            if saved_id != file_id:
                db.set_offset(self.conn, self.path, file_id, offset)
            return 0
        with open(self.path, "rb") as f:
            f.seek(offset)
            chunk = f.read(st.st_size - offset)
        end = chunk.rfind(b"\n")
        if end < 0:
            return 0
        n = 0
        self.conn.execute("BEGIN")
        try:
            for line in chunk[:end].split(b"\n"):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                    self.handler(self.conn, rec, self.default_host)
                    n += 1
                except (ValueError, KeyError, TypeError, AttributeError) as e:   # null·배열 줄도 그 줄만 건너뛴다
                    log.warning("스풀 줄 건너뜀 %s: %s", self.path.name, e)
            db.set_offset(self.conn, self.path, file_id, offset + end + 1)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return n


def tailers(conn, spool_dir, default_host):
    return [
        SpoolTailer(conn, spool_dir / "statusline.jsonl", ingest_statusline, default_host),
        SpoolTailer(conn, spool_dir / "usage_tool.jsonl", ingest_get_usage, default_host),
        SpoolTailer(conn, spool_dir / "session_events.jsonl", ingest_session_event, default_host),
    ]


def ingest_session_event(conn, rec, default_host):
    """세션 상태 hook 한 줄 → live_sessions (전이 규칙은 session_state.apply_event)."""
    # 1단계: 객체·세션 ID(문자열)·유한한 시각(숫자)이 아니면 버린다. tailer가 경고를 남기고 그 줄만 건너뛴다
    if not isinstance(rec, dict):
        raise ValueError("객체가 아님")
    if not isinstance(rec.get("session_id"), str) or not rec["session_id"]:
        raise ValueError("session_id 없음")
    ts = rec.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        raise ValueError("ts 없음")
    # 2단계: 글자 칸은 글자로 맞추고(숫자 문구 등), 호스트가 없으면 기본값을 붙인다
    ev = dict(rec)
    for key in ("event", "title", "cwd", "tool", "ntype", "message", "error", "agent_id", "host"):
        if ev.get(key) is not None and not isinstance(ev[key], str):
            ev[key] = str(ev[key])
    ev["host"] = ev.get("host") or default_host
    # 3단계: 지금 행에 이벤트를 적용해 다시 쓴다
    row = session_state.apply_event(db.get_live(conn, ev["session_id"]), ev)
    if row is not None:                    # SessionStart만 온 새 세션은 올리지 않는다
        db.put_live(conn, row)


def truncate_if_consumed(conn, path, max_bytes=5_000_000):
    """다 읽은 스풀이 max_bytes를 넘으면 비운다. 비웠으면 True.

    tailer는 '크기 < 저장된 오프셋'이면 처음부터 읽으므로, 오프셋도 0으로 맞춰 둔다."""
    try:
        st = path.stat()
    except OSError:
        return False
    saved_id, offset = db.get_offset(conn, path)
    if st.st_size <= max_bytes or offset != st.st_size or saved_id != str(st.st_ino):
        return False
    os.truncate(path, 0)
    db.set_offset(conn, path, saved_id, 0)
    return True
