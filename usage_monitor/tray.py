"""트레이 프로그램. 수신기·위젯 창·트레이 아이콘을 한 프로세스에서 돌린다.

    pythonw -m usage_monitor run

스레드 구성
  주 스레드    위젯 창(pywebview). 창을 숨겨도 이 루프는 돈다
  수신기       uvicorn. 죽으면 5초 뒤 다시 띄운다(작업 스케줄러 감시를 대신한다)
  트레이       pystray. 아이콘에 5시간 한도 %를 그리고 30초마다 갱신한다
  세션 상태    수신기 /stream(SSE)을 구독해 배지·툴팁을 바로 바꾸고, 위젯 띄우기를 정한다(alerts.py)
  종료 감시    `usage-monitor quit`이 올리는 이름 있는 이벤트를 기다린다
"""
import ctypes
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser

import pystray
import uvicorn
import webview
from PIL import Image, ImageDraw, ImageFont

from . import alerts, autostart, config, connect, kiosk, receiver, widget_host

log = logging.getLogger("usage-monitor.app")
APP_MUTEX = "usage-monitor-app"
QUIT_EVENT = "usage-monitor-quit"
ICON_REFRESH_SEC = 30
COLORS = {"good": (12, 163, 12), "warning": (250, 178, 25), "critical": (208, 59, 59), "none": (95, 94, 90)}
BADGE_COLORS = {"alert": (240, 140, 26), "done": (12, 163, 12)}   # web/tokens.css의 --st-waiting · --st-done
TOOLTIP_MAX = 127                                                  # Windows 트레이 툴팁 길이 한도
SSE_RETRY_SEC = 3


def fetch_snapshot(base, token=""):
    try:
        with urllib.request.urlopen(config.with_token(base + "/api/snapshot", token), timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def pct_from_snapshot(snap):
    """스냅샷의 5시간 한도 %. 모르거나 리셋됐으면 None."""
    lim = (snap or {}).get("limits", {}).get("5h")
    return lim["pct"] if lim and lim["verdict"] != "reset" else None


def make_icon(pct, badge=(None, "")):
    """5시간 한도 %를 숫자로 그린 64×64 아이콘. 값을 모르면 회색 줄표.
    badge가 있으면 오른쪽 위에 세션 상태 배지를 덧그린다(alert: 주황 원 + 개수, done: 초록 점)."""
    level = "none" if pct is None else "critical" if pct >= 90 else "warning" if pct >= 70 else "good"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=12, fill=(26, 26, 25, 255))
    d.rounded_rectangle((6, 50, 57, 58), radius=4, fill=COLORS[level])       # 색만이 아니라 숫자도 함께 준다
    text = "–" if pct is None else str(min(pct, 99) if pct < 100 else 100)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40 if len(text) < 3 else 30)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    d.text(((64 - box[2] + box[0]) / 2 - box[0], (48 - box[3] + box[1]) / 2 - box[1]), text,
           font=font, fill=(232, 231, 225, 255))
    kind, text = badge
    if kind == "alert":
        # 1) 주황 원(지름 26)과 흰 개수
        d.ellipse((37, 0, 63, 26), fill=BADGE_COLORS["alert"] + (255,), outline=(26, 26, 25, 255), width=2)
        try:
            bfont = ImageFont.truetype("segoeuib.ttf", 17 if len(text) < 2 else 13)
        except OSError:
            bfont = ImageFont.load_default()
        bb = d.textbbox((0, 0), text, font=bfont)
        d.text((50 - (bb[2] + bb[0]) / 2, 13 - (bb[3] + bb[1]) / 2), text, font=bfont, fill=(255, 255, 255, 255))
    elif kind == "done":
        # 2) 확인 안 한 완료만 있으면 초록 점
        d.ellipse((48, 2, 62, 16), fill=BADGE_COLORS["done"] + (255,), outline=(26, 26, 25, 255), width=2)
    return img


def tooltip(snap):
    if not snap:
        return "Usage Monitor · 수신기 응답 없음"
    parts = []
    for key, label in (("5h", "5시간"), ("7d", "7일")):
        lim = snap["limits"].get(key)
        if lim and lim["verdict"] != "reset":
            parts.append(f"{label} {lim['pct']}%")
    text = "Usage Monitor · " + (" · ".join(parts) if parts else "한도 관측 없음")
    live = alerts.tooltip_part(snap.get("live"))
    if live:
        text += " · " + live
    return text[:TOOLTIP_MAX]


class Receiver:
    """이미 응답하는 수신기가 있으면 그것을 쓰고, 없으면 이 프로세스에서 띄운다."""

    def __init__(self, cfg, base):
        self.cfg, self.base = cfg, base
        self.server, self.stopping = None, False

    def start(self):
        if fetch_snapshot(self.base, self.cfg.auth_token):
            log.info("이미 떠 있는 수신기를 씁니다 %s", self.base)
            return
        threading.Thread(target=self._serve, name="receiver", daemon=True).start()

    def _serve(self):
        while not self.stopping:
            try:
                self.server = uvicorn.Server(uvicorn.Config(
                    receiver.create_app(self.cfg), host=self.cfg.bind, port=self.cfg.port, log_level="warning"))
                self.server.run()
            except BaseException as e:          # 포트 충돌이면 uvicorn이 SystemExit을 낸다
                log.error("수신기 종료: %r", e)
            if not self.stopping:
                time.sleep(5)

    def stop(self):
        self.stopping = True
        if self.server:
            self.server.should_exit = True


class App:
    def __init__(self):
        self.cfg = config.load()
        receiver.setup_logging(self.cfg)
        log.info("트레이 프로그램 시작 pid=%s %s", os.getpid(), sys.executable)
        self.base = f"http://127.0.0.1:{self.cfg.port}"
        self.settings_path = self.cfg.home / "state" / "app.json"
        self.settings = {"kiosk_on_start": False, "auto_show_widget": True, **self._load_settings()}
        self.receiver = Receiver(self.cfg, self.base)
        self.widget_state = widget_host.load_state()
        self.window = self.api = self.icon = None
        self.quitting = False
        # 세션 상태: 마지막 스냅샷의 live, 아이콘에 쓸 마지막 5시간 %, 토스트 결정기
        self.last_live = None
        self.last_pct = None
        self.tracker = alerts.AlertTracker()

    def url(self, path):
        """수신기 주소. auth_token을 설정했으면 ?token= 을 붙인다."""
        return config.with_token(self.base + path, self.cfg.auth_token)

    def _load_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings), encoding="utf-8")

    # ── 트레이 메뉴 동작 ──────────────────────────────
    def toggle_widget(self, *_):
        widget_host.set_visible(self.api, self.widget_state, not self.widget_state.get("visible", True))

    def set_widget_opacity(self, percent):
        def action(*_):
            widget_host.set_opacity(self.api, self.widget_state, percent)
            self.window.evaluate_js(f"window.syncOpacity && window.syncOpacity({percent})")   # 위젯 버튼의 표시를 맞춘다
        return action

    def set_widget_scale(self, scale):
        def action(*_):
            widget_host.set_scale(self.api, self.widget_state, scale)
            self.window.evaluate_js(f"window.syncScale && window.syncScale({scale})")   # 페이지가 글자·창 크기를 맞춘다
        return action

    def open_dashboard(self, *_):
        webbrowser.open(self.url("/"))

    def open_full(self, *_):
        ok, msg = kiosk.open_full(self.url("/full"))
        if not ok:
            self.notify(msg)

    def open_sessions(self, *_):
        webbrowser.open(self.url("/sessions"))

    def toggle_auto_show(self, *_):
        self.settings["auto_show_widget"] = not self.settings["auto_show_widget"]
        self._save_settings()

    def open_kiosk(self, *_):
        ok, msg = kiosk.open_kiosk(self.url("/"))
        if not ok:
            self.notify(msg)

    def toggle_kiosk_on_start(self, *_):
        self.settings["kiosk_on_start"] = not self.settings["kiosk_on_start"]
        self._save_settings()

    def toggle_autostart(self, *_):
        autostart.disable() if autostart.is_enabled() else autostart.enable()

    def toggle_connection(self, *_):
        if self.connected():
            report = connect.disconnect()
            self.notify("Claude Code 연결을 해제했습니다")
        else:
            report = connect.connect(port=self.cfg.port)
            skipped = report["skipped"]
            self.notify("Claude Code에 연결했습니다" + (" · " + " / ".join(skipped) if skipped else ""))
        log.info("연결 변경: %s", report)

    @staticmethod
    def connected():
        st = connect.status()
        return bool(st["hooks"]) and st["scripts"]

    def notify(self, message):
        try:
            self.icon.notify(message, "Usage Monitor")
        except Exception:
            log.info("알림: %s", message)

    def quit(self, *_):
        if self.quitting:
            return
        self.quitting = True
        self.receiver.stop()
        if self.icon:
            self.icon.stop()
        widget_host.destroy(self.api)

    # ── 구성 요소 ────────────────────────────────────
    def build_icon(self):
        item, sep = pystray.MenuItem, pystray.Menu.SEPARATOR
        menu = pystray.Menu(
            # 아이콘을 누르면 기본 항목이 실행된다. 위젯을 보이고 숨긴다
            item("위젯 보이기", self.toggle_widget, default=True,
                 checked=lambda _: self.widget_state.get("visible", True)),
            item("위젯 투명도", pystray.Menu(*[
                item(f"{p}%", self.set_widget_opacity(p), radio=True,
                     checked=lambda _, p=p: self.widget_state.get("opacity", 100) == p)
                for p in widget_host.OPACITY_LEVELS])),
            item("위젯 크기 배율", pystray.Menu(*[
                item(f"{round(sc * 100)}%" + (" (원래대로)" if sc == 1.0 else ""), self.set_widget_scale(sc), radio=True,
                     checked=lambda _, sc=sc: abs(self.widget_state.get("scale", 1.0) - sc) < 0.001)
                for sc in widget_host.SCALE_LEVELS])),
            item("대시보드 열기 (브라우저)", self.open_dashboard),
            item("세션 상태 열기 (브라우저)", self.open_sessions),
            item("전체화면 대시보드", self.open_full),
            item("키오스크 열기 (바 모니터)", self.open_kiosk),
            sep,
            item("대기 생기면 위젯 띄우기", self.toggle_auto_show,
                 checked=lambda _: self.settings["auto_show_widget"]),
            item("시작할 때 키오스크 열기", self.toggle_kiosk_on_start,
                 checked=lambda _: self.settings["kiosk_on_start"]),
            item("로그인 시 자동 실행", self.toggle_autostart, checked=lambda _: autostart.is_enabled()),
            item("Claude Code에 연결", self.toggle_connection, checked=lambda _: self.connected()),
            sep,
            item("종료", self.quit),
        )
        return pystray.Icon("usage-monitor", make_icon(None), "Usage Monitor", menu)

    def refresh_loop(self):
        kiosk_pending = self.settings["kiosk_on_start"]
        while not self.quitting:
            snap = fetch_snapshot(self.base, self.cfg.auth_token)
            self.last_pct = pct_from_snapshot(snap)
            if snap and self.last_live is None:    # 세션 목록은 SSE가 최신이다. 늦게 끝난 조회가 덮지 않게 처음만
                self.last_live = snap.get("live")
            self.redraw(snap)
            if snap and kiosk_pending:           # 수신기가 응답한 뒤에 띄운다. 먼저 띄우면 오류 페이지에 머문다
                kiosk_pending = False
                self.open_kiosk()
            for _ in range(ICON_REFRESH_SEC if snap else 3):
                if self.quitting:
                    return
                time.sleep(1)

    def redraw(self, snap):
        """아이콘(한도 % + 세션 배지)과 툴팁을 다시 그린다. snap이 None이면 툴팁은 '수신기 응답 없음'."""
        try:
            self.icon.icon = make_icon(self.last_pct, alerts.badge(self.last_live))
            self.icon.title = tooltip(snap)
        except Exception:
            log.exception("트레이 아이콘 갱신 실패")

    def live_loop(self):
        """수신기 SSE를 구독한다. 스냅샷마다 배지를 바꾸고, 새 대기가 생기면 숨긴 위젯을 띄운다."""
        while not self.quitting:
            try:
                with urllib.request.urlopen(self.url("/stream"), timeout=60) as resp:
                    lines = (raw.decode("utf-8", "replace") for raw in resp)
                    for event, data in alerts.parse_sse(lines):
                        if self.quitting:
                            return
                        if event != "snapshot":
                            continue
                        # 1) 세션 상태 반영
                        self.last_live = data.get("live") or {"items": [], "counts": {}, "unseen": {}}
                        self.last_pct = pct_from_snapshot(data)     # 아이콘 숫자도 SSE로 바로 갱신
                        wake = self.tracker.update(self.last_live["items"], time.time())
                        # 2) 새로 대기·결정 필요가 생겼고 설정이 켜져 있으면 위젯을 보인다
                        if wake and self.settings["auto_show_widget"] and not self.widget_state.get("visible", True):
                            widget_host.set_visible(self.api, self.widget_state, True)
                        self.redraw(data)
            except Exception as e:                 # 수신기 재시작·타임아웃(하트비트 10초라 60초면 끊긴 것)
                log.debug("세션 SSE 다시 연결: %s", e)
            for _ in range(SSE_RETRY_SEC):
                if self.quitting:
                    return
                time.sleep(1)

    def quit_watch(self):
        kernel32 = ctypes.windll.kernel32
        event = kernel32.CreateEventW(None, True, False, QUIT_EVENT)
        kernel32.WaitForSingleObject(event, 0xFFFFFFFF)
        self.quit()

    def run(self):
        self.receiver.start()
        widget_host.BASE = self.base
        widget_host.TOKEN = self.cfg.auth_token
        self.window, self.api = widget_host.create(
            self.widget_state, on_close=lambda: widget_host.set_visible(self.api, self.widget_state, False))
        self.icon = self.build_icon()
        self.icon.run_detached()
        threading.Thread(target=self.refresh_loop, name="tray-refresh", daemon=True).start()
        threading.Thread(target=self.live_loop, name="tray-live", daemon=True).start()
        threading.Thread(target=self.quit_watch, name="quit-watch", daemon=True).start()
        webview.start(widget_host.run, (self.window, self.api, self.widget_state))
        self.quit()                              # 창 루프가 다른 이유로 끝났을 때의 정리


def request_quit():
    """떠 있는 트레이 프로그램에 종료를 알린다. 떠 있지 않으면 False."""
    kernel32 = ctypes.windll.kernel32
    event = kernel32.OpenEventW(0x0002, False, QUIT_EVENT)         # EVENT_MODIFY_STATE
    if not event:
        return False
    kernel32.SetEvent(event)
    kernel32.CloseHandle(event)
    return True


def is_running():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenMutexW(0x00100000, False, APP_MUTEX)     # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
    return bool(handle)


def main():
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    if ctypes.windll.kernel32.GetLastError() == 183:               # 이미 떠 있다
        return
    App().run()
    del mutex


if __name__ == "__main__":
    main()
