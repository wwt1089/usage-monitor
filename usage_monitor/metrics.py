"""화면용 파생 지표. 분 단위 버킷은 저장하지 않고 조회할 때 계산한다."""
import time

from . import db, session_state

WINDOW_LEN = {"5h": 5 * 3600}          # 그 밖의 창('7d', '7d:<모델>')은 7일
TREND_SPAN = {"5h": 3600}              # 추세 표본 구간. 그 밖은 6시간
IDLE_AFTER_SEC = 600                   # 최근 활동이 이보다 오래되면 유휴 화면

TOKEN_TYPES = (("input", "input_tokens"), ("output", "output_tokens"),
               ("cache_read", "cache_read"), ("cache_creation", "cache_creation"))


def _slope(points):
    n = len(points)
    mx = sum(t for t, _ in points) / n
    my = sum(p for _, p in points) / n
    den = sum((t - mx) ** 2 for t, _ in points)
    return sum((t - mx) * (p - my) for t, p in points) / den if den else 0.0


def _spent_since(conn, ts):
    """ts(초) 뒤에 들어온 요청의 추정 비용과 건수."""
    r = conn.execute(
        "SELECT COUNT(*) AS n, SUM(COALESCE(cost_micros_est,0)) AS cost FROM api_requests WHERE ts > ?",
        (ts * 1000,),
    ).fetchone()
    return {"cost_micros_est": r["cost"] or 0, "requests": r["n"]}


def since_observation(conn):
    """마지막 한도 관측(5h·7d) 뒤의 추정 비용. hook이 다음 관측 시점을 정할 때 읽는다."""
    t = conn.execute("SELECT MAX(ts) AS t FROM limit_observations WHERE window IN ('5h','7d')").fetchone()["t"]
    return None if t is None else {"obs_ts": t, **_spent_since(conn, t)}


def limit_state(conn, window, now):
    """한 창의 현재 값·ETA. 관측 시점 기준."""
    length = WINDOW_LEN.get(window, 7 * 86400)
    span = TREND_SPAN.get(window, 6 * 3600)
    row = conn.execute(
        "SELECT resets_at FROM limit_observations WHERE window=? ORDER BY ts DESC LIMIT 1", (window,)
    ).fetchone()
    if not row:
        return None
    resets_at = row["resets_at"]
    rows = conn.execute(
        "SELECT ts, used_pct, source FROM limit_observations WHERE window=? AND resets_at=? ORDER BY ts",
        (window, resets_at),
    ).fetchall()
    # 한 창 안에서 사용량은 줄지 않는다. 늦게 도착한 낮은 값(오래된 세션)은 누적 최대값으로 덮는다
    series, peak = [], 0
    for r in rows:
        peak = max(peak, r["used_pct"])
        series.append((r["ts"], peak))
    t_last, pct = series[-1]
    out = {
        "window": window, "pct": pct, "resets_at": resets_at, "last_obs_ts": t_last,
        "sources": sorted({r["source"] for r in rows}), "method": None,
        "rate_pct_per_hour": None, "eta_ts": None, "verdict": None,
        "since_obs": _spent_since(conn, t_last),     # 한도 값이 실제보다 얼마나 뒤처졌는지 가늠하는 값
    }
    if resets_at <= now:
        out["verdict"] = "reset"          # 창이 끝났다. 새 관측 전까지 현재 값을 모른다
        return out
    if pct >= 100:
        out.update(verdict="exhausted", eta_ts=t_last)
        return out
    if pct == 0:
        out.update(verdict="no_usage", rate_pct_per_hour=0.0)
        return out
    sample = [(t, p) for t, p in series if t >= now - span]
    rate = _slope(sample) if len(sample) >= 3 else 0.0
    if rate > 0:
        out["method"] = "trend"
    else:
        elapsed = t_last - (resets_at - length)
        if elapsed <= 0:
            return out
        rate = pct / elapsed
        out["method"] = "window_avg"
    eta = t_last + (100 - pct) / rate
    out.update(
        rate_pct_per_hour=rate * 3600, eta_ts=int(eta),
        verdict="exhaust_before_reset" if eta < resets_at else "safe",
    )
    return out


def burn(conn, now, minutes=60):
    """최근 N분, 1분 버킷, 토큰 타입별 합계. 모든 host 합산."""
    first = (int(now) // 60 - (minutes - 1)) * 60
    series = {name: [0] * minutes for name, _ in TOKEN_TYPES}
    rows = conn.execute(
        f"""SELECT ts / 60000 AS m, {', '.join(f'SUM(COALESCE({col},0)) AS {name}' for name, col in TOKEN_TYPES)}
            FROM api_requests WHERE ts >= ? GROUP BY m""",
        (first * 1000,),
    ).fetchall()
    for r in rows:
        i = r["m"] - first // 60
        if 0 <= i < minutes:
            for name, _ in TOKEN_TYPES:
                series[name][i] = r[name]
    return {"start_ts": first, "minutes": minutes, "series": series}


def today(conn, now):
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    r = conn.execute(
        f"""SELECT COUNT(*) AS requests, SUM(COALESCE(cost_micros_est,0)) AS cost_micros_est,
                   {', '.join(f'SUM(COALESCE({col},0)) AS {name}' for name, col in TOKEN_TYPES)}
            FROM api_requests WHERE ts >= ?""",
        (int(midnight * 1000),),
    ).fetchone()
    return {k: r[k] or 0 for k in r.keys()} | {"since_ts": int(midnight)}


def health(conn, now):
    """수집 상태. statusline·get_usage: 2분 live / 30분 recent / 그 이상 idle.
    otel: 같은 host의 다른 소스가 2분 안에 들어왔는데 otel이 2분 넘게 없으면 missing."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM source_health ORDER BY host, source")]
    # Claude를 쓰고 있는지는 statusline·get_usage로만 본다. appmeta는 앱 파일만 있으면 2초마다 갱신된다
    active_hosts = {r["host"] for r in rows
                    if r["source"] not in ("otel", "appmeta") and now - r["last_seen"] < 120}
    for r in rows:
        age = now - r["last_seen"]
        if age < 120:
            r["status"] = "live"
        elif r["source"] == "otel":
            r["status"] = "missing" if r["host"] in active_hosts else "idle"
        else:
            r["status"] = "recent" if age < 1800 else "idle"
    return rows


def build_snapshot(conn, now=None):
    now = now or time.time()
    windows = [r["window"] for r in conn.execute(
        "SELECT DISTINCT window FROM limit_observations WHERE ts >= ?", (int(now) - 8 * 86400,))]
    limits = {w: s for w in windows if (s := limit_state(conn, w, now))}
    last_req_ts = conn.execute("SELECT MAX(ts) AS t FROM api_requests").fetchone()["t"]
    last_sess = conn.execute("SELECT MAX(last_ts) AS t FROM session_state").fetchone()["t"]
    last_activity = max(filter(None, [last_req_ts and last_req_ts // 1000, last_sess]), default=None)
    hits = conn.execute(
        "SELECT COUNT(*) AS n, MAX(ts) AS last FROM limit_hits WHERE ts >= ?", ((int(now) - 5 * 3600) * 1000,)
    ).fetchone()
    sessions = [dict(r) for r in conn.execute(
        "SELECT * FROM session_state WHERE last_ts >= ? ORDER BY last_ts DESC LIMIT 5", (int(now) - 1800,))]
    # 세션 상태에 모델이 없을 때(OTel을 보내지 않는 세션) 화면이 쓰는 대체값
    last_req = conn.execute(
        "SELECT model, effort, client, ts FROM api_requests ORDER BY ts DESC LIMIT 1").fetchone()
    return {
        "now": int(now),
        "mode": "active" if last_activity and now - last_activity < IDLE_AFTER_SEC else "idle",
        "last_activity_ts": last_activity,
        "limits": limits,
        "burn": burn(conn, now),
        "today": today(conn, now),
        "sessions": sessions,
        "last_request": dict(last_req) if last_req else None,
        "health": health(conn, now),
        "limit_hits": {"count_5h": hits["n"], "last_ts": hits["last"]},
        "live": live_snapshot(conn, now),
    }


LIVE_ITEM_KEYS = ("session_id", "app_session_id", "title", "cwd", "state", "detail", "state_ts",
                  "last_event_ts")
UNSEEN_STATES = ("waiting", "decision", "done", "error")


def live_snapshot(conn, now):
    """세션 상태 목록. 보이는 세션만, 상태 우선순위(완료는 아래) → 확인 안 함 → 최근 순."""
    # 1단계: 보일 세션만 골라 정렬한다(보관된 세션·오래전에 확인한 세션은 빠진다)
    rows = sorted((r for r in db.list_live(conn) if session_state.is_visible(r, now)), key=session_state.sort_key)
    # 2단계: 상태별 개수와 확인 안 한 개수
    counts = {st: 0 for st in session_state.STATES}
    unseen = {st: 0 for st in UNSEEN_STATES}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        if not r["seen"] and r["state"] in unseen:
            unseen[r["state"]] += 1
    # 3단계: 화면이 쓰는 값만 내보낸다
    items = [dict({k: r[k] for k in LIVE_ITEM_KEYS}, seen=bool(r["seen"]),
                  stale=session_state.is_stale(r, now)) for r in rows]
    return {"items": items, "counts": counts, "unseen": unseen}
