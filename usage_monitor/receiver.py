"""FastAPI 수신기. 실행: python -m usage_monitor receiver

- POST /v1/logs      OTLP/JSON 로그 수신 → api_requests UPSERT, 429 → limit_hits
- GET  /api/snapshot 화면 최초 로드용 전체 상태
- GET  /stream       SSE. 변경 시 스냅샷, 10초마다 heartbeat, 30초마다 스냅샷(시간이 흐르며 바뀌는 값용)
- GET  /             정적 대시보드(web/)
- GET  /sessions     세션 상태 표(web/sessions.html)
- GET  /full         일반 모니터 전체화면 대시보드(web/full.html)
- POST /api/sessions/{id}/seen  세션 상태를 "확인함"으로
백그라운드: 스풀 tail(0.5초), 앱 세션 메타데이터(2초, appmeta.py), 집계(5분, rollup.py), 보관 기간 정리(하루 1회)
"""
import asyncio
import gzip
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as config_mod
from . import appmeta, db, metrics, otlp, rollup, session_state, spool
from .sse import Hub

log = logging.getLogger("usage-monitor")
WEB_DIR = config_mod.WEB_DIR

HEARTBEAT_SEC = 10
SNAPSHOT_REFRESH_SEC = 30
CHANGE_DEBOUNCE_SEC = 0.3
PURGE_EVERY_SEC = 86400
ROLLUP_EVERY_SEC = 300
RUN_TOUCH_SEC = 60
APPMETA_POLL_SEC = 2


class FreshStaticFiles(StaticFiles):
    """화면 파일을 고치면 키오스크·배경화면·위젯 창이 다음 로드에 바로 새 파일을 받게 한다.
    헤더가 없으면 브라우저가 Last-Modified로 추정해 한동안 캐시본을 쓴다."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = db.connect(cfg.db_path)
        self.hub = Hub()
        self.dirty = asyncio.Event()
        self.tailers = spool.tailers(self.conn, cfg.spool_dir, cfg.default_host)
        self.since_obs_written = None
        self.appmeta = appmeta.AppMetaReader(cfg.appmeta_glob or appmeta.default_glob())

    def snapshot(self):
        return metrics.build_snapshot(self.conn)

    def write_since_obs(self):
        """hook이 읽는 state/since_obs.json. 마지막 한도 관측 뒤의 추정 비용과 관측 기준 금액을 담는다."""
        since = metrics.since_observation(self.conn)
        if not since:
            return
        body = json.dumps({"obs_ts": since["obs_ts"], "cost_usd": round(since["cost_micros_est"] / 1e6, 4),
                           "requests": since["requests"], "threshold_usd": self.cfg.obs_cost_usd})
        if body == self.since_obs_written:
            return
        path = self.cfg.home / "state" / "since_obs.json"
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, path)               # hook이 반쯤 쓰인 파일을 읽지 않게 한 번에 바꾼다
            self.since_obs_written = body
        except OSError as e:
            log.warning("since_obs.json 쓰기 실패: %s", e)

    def mark_changed(self):
        self.dirty.set()


async def spool_loop(st):
    while True:
        try:
            if sum(t.poll() for t in st.tailers):
                st.mark_changed()
        except Exception:
            log.exception("스풀 tail 실패")
        await asyncio.sleep(st.cfg.spool_poll_sec)


async def publish_loop(st):
    """변경은 0.3초 모아서, 변경이 없어도 30초마다 스냅샷을 보낸다."""
    last_snapshot = 0.0
    last_beat = time.time()
    while True:
        try:
            await asyncio.wait_for(st.dirty.wait(), timeout=1.0)
            await asyncio.sleep(CHANGE_DEBOUNCE_SEC)
        except asyncio.TimeoutError:
            pass
        now = time.time()
        if st.dirty.is_set() or now - last_snapshot >= SNAPSHOT_REFRESH_SEC:
            st.dirty.clear()
            st.write_since_obs()
            if st.hub.clients:
                st.hub.publish("snapshot", st.snapshot())
            last_snapshot = now
        if now - last_beat >= HEARTBEAT_SEC:
            st.hub.publish("heartbeat", {"now": int(now)})
            last_beat = now


def apply_appmeta(st, now):
    """앱 메타데이터를 다시 읽고, 세션 상태 행에 보강을 적용한다. 바뀐 행이 있으면 True."""
    # 1단계: 바뀐 파일만 읽는다
    st.appmeta.poll(now)
    if st.appmeta.files_seen:
        db.touch_health(st.conn, st.cfg.default_host, "appmeta", int(now))
    # 2단계: 모든 행에 최신 메타데이터를 적용한다(행이 hook으로 바뀐 뒤에도 다시 맞추도록 매번 전부)
    changed = False
    for row in db.list_live(st.conn):
        meta = st.appmeta.cache.get(row["session_id"])
        if not meta:
            continue
        new = session_state.apply_meta(row, meta)
        if new != row:
            db.put_live(st.conn, new)
            changed = True
    return changed


async def appmeta_loop(st):
    """앱 세션 메타데이터로 '결정 필요'·'열어 봄'·제목을 보강한다. 설정으로 끌 수 있다."""
    if not st.cfg.appmeta_enabled:
        return
    db.ensure_health(st.conn, st.cfg.default_host, "appmeta")
    while True:
        try:
            if apply_appmeta(st, time.time()):
                st.mark_changed()
        except Exception:
            log.exception("앱 메타데이터 반영 실패")
        await asyncio.sleep(APPMETA_POLL_SEC)


async def rollup_loop(st):
    """가동 구간을 1분마다 남기고, 5분마다 최근 이틀을 다시 집계한다."""
    run_id = rollup.start_run(st.conn)
    last_rollup = 0.0
    while True:
        now = time.time()
        try:
            rollup.touch_run(st.conn, run_id, now)
            if now - last_rollup >= ROLLUP_EVERY_SEC:
                rollup.rollup(st.conn, now)
                last_rollup = now
        except Exception:
            log.exception("집계 실패")
        await asyncio.sleep(RUN_TOUCH_SEC)


async def purge_loop(st):
    while True:
        try:
            rollup.rollup(st.conn)                # 지우기 전에 집계를 맞춘다
            db.purge_old(st.conn, st.cfg.retention_days)
            spool.truncate_if_consumed(st.conn, st.cfg.spool_dir / "session_events.jsonl")
        except Exception:
            log.exception("보관 기간 정리 실패")
        await asyncio.sleep(PURGE_EVERY_SEC)


def create_app(cfg=None):
    cfg = cfg or config_mod.load()

    @asynccontextmanager
    async def lifespan(app):
        st = State(cfg)
        app.state.st = st
        tasks = [asyncio.create_task(f(st)) for f in (spool_loop, publish_loop, rollup_loop, purge_loop, appmeta_loop)]
        log.info("수신기 시작 %s:%s db=%s spool=%s", cfg.bind, cfg.port, cfg.db_path, cfg.spool_dir)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            st.conn.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.post("/v1/logs")
    async def receive_logs(request: Request):
        if cfg.auth_token and request.headers.get("authorization") != f"Bearer {cfg.auth_token}":
            raise HTTPException(401)
        if "json" not in request.headers.get("content-type", ""):
            raise HTTPException(415, "http/json만 받는다 (OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/json)")
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "JSON 파싱 실패")
        st = request.app.state.st
        n = 0
        st.conn.execute("BEGIN")
        try:
            hosts = set()
            for kind, row in otlp.parse(payload, cfg.default_host):
                hosts.add(row["host"])
                if kind == "api_request":
                    if not row["request_id"] or row["ts"] is None:
                        continue
                    db.upsert_request(st.conn, row)
                    db.upsert_session(st.conn, row["session_id"], row["ts"] // 1000, host=row["host"],
                                      client=row["client"], model=row["model"], effort=row["effort"])
                    n += 1
                elif kind == "user_prompt":
                    if row["prompt_id"] and row["ts"] is not None:
                        db.upsert_user_prompt(st.conn, row)
                        n += 1
                elif row.get("status_code") == 429:
                    db.insert_limit_hit(st.conn, row["ts"], row["host"], row["session_id"], row["error"])
                    n += 1
            # 어떤 이벤트든 받았으면 수집 경로는 살아 있다
            for h in hosts or {cfg.default_host}:
                db.touch_health(st.conn, h, "otel")
            st.conn.execute("COMMIT")
        except Exception:
            st.conn.execute("ROLLBACK")
            raise
        if n:
            st.mark_changed()
        return JSONResponse({"partialSuccess": {}})

    @app.post("/api/sessions/{session_id}/seen")
    async def mark_seen(session_id: str, request: Request):
        """위젯·대시보드에서 세션을 눌렀을 때. 확인함으로 표시한다."""
        if cfg.auth_token and request.headers.get("authorization") != f"Bearer {cfg.auth_token}":
            raise HTTPException(401)
        st = request.app.state.st
        if not db.mark_seen(st.conn, session_id):
            raise HTTPException(404)
        st.mark_changed()
        return {"ok": True}

    @app.get("/api/snapshot")
    async def snapshot(request: Request):
        return request.app.state.st.snapshot()

    @app.get("/stream")
    async def stream(request: Request):
        st = request.app.state.st
        first = f"event: snapshot\ndata: {json.dumps(st.snapshot(), ensure_ascii=False, separators=(',', ':'))}\n\n"
        return StreamingResponse(
            st.hub.stream(first), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/full")
    async def full_page():
        """일반 모니터 전체화면 대시보드(한도·토큰 + 세션 상태 표)."""
        return FileResponse(WEB_DIR / "full.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sessions")
    async def sessions_page():
        """브라우저용 세션 상태 표. 키오스크 화면(/)은 1920×480 고정이라 따로 둔다."""
        return FileResponse(WEB_DIR / "sessions.html", headers={"Cache-Control": "no-cache"})

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    if WEB_DIR.exists():
        app.mount("/web", FreshStaticFiles(directory=WEB_DIR), name="web")
    return app


def setup_logging(cfg):
    """로그를 데이터 폴더의 receiver.log에 남긴다(1MB×3 회전). 콘솔이 있으면 화면에도 낸다."""
    log_path = cfg.home / "receiver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stdout is None or sys.stderr is None:
        # pythonw(자동 실행)에는 콘솔이 없다. uvicorn이 stderr에 쓰다 죽지 않게 파일로 돌린다
        sys.stdout = sys.stderr = open(log_path.with_suffix(".stdio.log"), "a", encoding="utf-8", buffering=1)
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def main():
    cfg = config_mod.load()
    setup_logging(cfg)
    uvicorn.run(create_app(cfg), host=cfg.bind, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
