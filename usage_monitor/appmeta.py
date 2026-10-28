"""Claude 데스크톱 앱이 세션마다 쓰는 메타데이터(local_*.json)를 읽는다.

    %LOCALAPPDATA%\\Claude-Data\\<계정>\\claude-code-sessions\\<…>\\<…>\\local_<id>.json

문서화된 형식이 아니다. 앱 업데이트로 바뀌면 읽지 못한 파일은 건너뛰고, 세션 상태는 hook만으로 동작한다.
쓰는 값: 제목, 앱의 턴 요약(blocked = 결정 필요), 세션을 마지막으로 연 시각, 보관 여부.
"""
import glob
import json
import logging
import os

log = logging.getLogger(__name__)


def default_glob():
    """앱 메타데이터 파일 패턴. LOCALAPPDATA가 없으면(다른 OS) 빈 문자열."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return ""
    return os.path.join(base, "Claude-Data", "*", "claude-code-sessions", "*", "*", "local_*.json")


def _int(value):
    """숫자가 아니거나 없으면 0."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def parse_meta(data):
    """파일 내용(dict) → 세션 상태에 쓰는 값. hook 세션 ID(cliSessionId)가 없으면 None."""
    if not isinstance(data, dict) or not isinstance(data.get("cliSessionId"), str) or not data["cliSessionId"]:
        return None
    summary = data.get("postTurnSummary") if isinstance(data.get("postTurnSummary"), dict) else {}
    last_uuid, summarized = data.get("lastAssistantUuid"), summary.get("summarizes_uuid")
    return {
        "cli_session_id": data["cliSessionId"],
        "app_session_id": data.get("sessionId"),
        "title": data.get("title") or None,
        "status_category": summary.get("status_category"),
        "needs_action": summary.get("needs_action") or None,
        # 요약이 지금의 마지막 응답을 다룬 것일 때만 믿는다(턴이 진행 중이면 이전 요약이 남아 있다)
        "summary_fresh": bool(last_uuid and summarized and last_uuid == summarized),
        "last_turn_edge_ms": _int(data.get("lastTurnEdgeAt")),
        "last_focused_ms": _int(data.get("lastFocusedAt")),
        "archived": bool(data.get("isArchived")),
    }


class AppMetaReader:
    """바뀐 파일만 다시 읽어 cache(cliSessionId → parse_meta 결과)를 갱신한다."""

    def __init__(self, pattern, rescan_sec=60.0):
        self.pattern = pattern
        self.rescan_sec = rescan_sec
        self.cache = {}
        self.files_seen = 0            # 마지막 폴링에서 읽을 수 있었던 파일 수(수집 상태 표시용)
        self._paths = []
        self._last_scan = None
        self._signatures = {}          # 경로 → (mtime_ns, 크기). 제대로 읽은 파일만 기억한다
        self._usable = {}              # 경로 → 세션 정보를 얻었는지(cliSessionId가 있었는지)
        self._owner = {}               # 경로 → 그 파일이 채운 cache 키. 파일이 사라지거나 바뀌면 지운다

    def _rescan(self, now):
        """파일 목록은 rescan_sec마다 다시 찾는다(glob은 비싸다)."""
        if self._last_scan is None or now - self._last_scan >= self.rescan_sec:
            self._paths = glob.glob(self.pattern) if self.pattern else []
            self._last_scan = now

    def poll(self, now):
        """바뀐 파일을 읽는다. cache가 바뀌었으면 True."""
        # 1단계: 파일 목록
        self._rescan(now)
        changed, readable = False, 0
        # 1-1단계: 목록에서 사라진 파일이 채운 항목은 지운다(앱에서 지운 세션)
        for path in [p for p in self._owner if p not in set(self._paths)]:
            changed |= self._drop(path)
        for path in self._paths:
            # 2단계: 크기·수정 시각이 그대로면 건너뛴다
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            if self._signatures.get(path) == sig:
                readable += self._usable.get(path, 0)
                continue
            # 3단계: 읽고 파싱한다. 앱이 쓰는 도중이면 깨져 있을 수 있으니 기억하지 않고 다음에 다시 읽는다
            try:
                with open(path, encoding="utf-8") as f:
                    meta = parse_meta(json.load(f))
            except (OSError, ValueError) as e:
                log.debug("앱 메타데이터 건너뜀 %s: %s", path, e)
                continue
            self._signatures[path] = sig
            self._usable[path] = 1 if meta else 0   # 형식이 바뀌어 세션 ID가 없으면 세지 않는다
            readable += self._usable[path]
            # 4단계: 이 파일이 예전에 다른 세션(또는 무효)이었으면 옛 항목을 지우고 새로 채운다
            if self._owner.get(path) != (meta["cli_session_id"] if meta else None):
                changed |= self._drop(path)
            if meta:
                self._owner[path] = meta["cli_session_id"]
                if self.cache.get(meta["cli_session_id"]) != meta:
                    self.cache[meta["cli_session_id"]] = meta
                    changed = True
        self.files_seen = readable
        return changed

    def _drop(self, path):
        """그 파일이 채운 cache 항목과 기록을 지운다. 지운 항목이 있었으면 True."""
        key = self._owner.pop(path, None)
        self._signatures.pop(path, None)
        self._usable.pop(path, None)
        return key is not None and self.cache.pop(key, None) is not None
