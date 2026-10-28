"""바탕화면 위젯 창. 트레이 프로그램(tray.py)이 띄운다. 단독 실행: python -m usage_monitor widget

수신기가 내주는 /web/widget.html 을 테두리 없는 항상-위 창에 띄운다.
- 수신기가 응답할 때까지 "대기 중" 화면을 보여 주고 3초마다 다시 확인한다
- 창을 끌어 옮긴 위치, 구성(mini | s | m | l), 투명도를 ~/.claude/usage-monitor/state/widget.json 에 기억한다
- 창 크기는 페이지(widget.js의 SIZES)가 CSS 픽셀로 정한다. 여기서는 그 값에 모니터 배율을 곱해 맞춘다
- × 버튼은 창을 끝내지 않고 숨긴다(트레이 메뉴에서 다시 보인다). 단독 실행일 때는 끝낸다

창 위치·크기는 pywebview가 아니라 Win32 API로 다룬다. pywebview의 테두리 없는 창은 요청한 크기에서
테두리 몫만큼 작게 만들어지고(300×232 → 284×193), 모니터 배율도 반영하지 않는다.
"""
import re
import ctypes
import json
import os
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import webview

HOME = Path(os.environ.get("USAGE_MONITOR_HOME") or Path.home() / ".claude" / "usage-monitor")
STATE_PATH = HOME / "state" / "widget.json"
BASE = os.environ.get("USAGE_MONITOR_URL", "http://127.0.0.1:4318").rstrip("/")
TITLE = "Usage Monitor"
DEFAULTS = {"size": "m", "w": 300, "h": 192, "x": None, "y": None, "visible": True,
            "opacity": 100, "scale": 1.0}   # w·h는 CSS 픽셀(배율 포함), x·y는 화면 픽셀, opacity는 불투명도 %, scale은 크기 배율
OPACITY_LEVELS = (100, 85, 70, 55, 40)  # 위젯 버튼과 트레이 메뉴가 고르는 단계. widget.js와 맞춘다
SCALE_LEVELS = (0.8, 1.0, 1.2, 1.5)     # 트레이 메뉴의 크기 배율. 그 사이 값은 위젯에서 Ctrl+마우스 휠로(50~200%)
SCALE_MIN, SCALE_MAX = 0.5, 2.0
MARGIN = 16                  # 처음 뜰 때 주 모니터 작업 영역의 오른쪽 아래에서 띄우는 거리
WAIT_HTML = (
    "<body style='margin:0;background:#1a1a19;color:#898781;font:13px system-ui,\"Malgun Gothic\";"
    "display:flex;align-items:center;justify-content:center;height:100vh'>usage-monitor 수신기 대기 중</body>"
)

user32 = ctypes.windll.user32
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_LAYERED = -20, 0x80, 0x40000, 0x80000
LWA_ALPHA = 0x2
SW_HIDE, SW_SHOWNA = 0, 8
WM_CLOSE = 0x0010


def load_state():
    try:
        return {**DEFAULTS, **json.loads(STATE_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


_STATE_LOCK = threading.Lock()        # 트레이 메뉴·페이지 API·창 이동 타이머가 함께 저장한다


def save_state(state):
    """위젯 설정을 저장한다. 여러 스레드가 겹쳐 써도 반쯤 쓰인 파일이 남지 않게 잠그고 한 번에 교체한다."""
    with _STATE_LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, STATE_PATH)


def find_hwnd():
    """이 프로세스가 만든, 보이는 위젯 창."""
    found = []
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buf, 64)
            if buf.value == TITLE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def window_pos(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top


def fit_size(hwnd, w_css, h_css):
    """창이 놓인 모니터의 배율에 맞춰 크기를 정한다. 150% 모니터에서 300×232는 450×348 픽셀이다."""
    scale = user32.GetDpiForWindow(hwnd) / 96
    user32.SetWindowPos(hwnd, None, 0, 0, round(w_css * scale), round(h_css * scale),
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)


def default_position(hwnd, w_css, h_css):
    """주 모니터 작업 영역(작업 표시줄 제외)의 오른쪽 아래."""
    area = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)       # SPI_GETWORKAREA
    user32.SetWindowPos(hwnd, None, area.left, area.top, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    scale = user32.GetDpiForWindow(hwnd) / 96                          # 주 모니터로 옮긴 뒤의 배율
    return (area.right - round(w_css * scale) - MARGIN, area.bottom - round(h_css * scale) - MARGIN)


def hide_from_taskbar(hwnd):
    """도구 창으로 바꿔 작업 표시줄·Alt+Tab에 나오지 않게 한다. 창 핸들을 다시 만들지 않는 방법이다
    (Form.ShowInTaskbar 를 바꾸면 핸들이 재생성돼 WebView2 초기화가 E_ABORT 로 실패한다)."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.ShowWindow(hwnd, SW_HIDE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    user32.ShowWindow(hwnd, SW_SHOWNA)


def apply_opacity(hwnd, percent):
    """창 전체의 불투명도. 계층 창(WS_EX_LAYERED)으로 바꾸고 알파 값을 준다. 30% 아래로는 내리지 않는다."""
    percent = max(30, min(100, int(percent)))
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(hwnd, 0, round(255 * percent / 100), LWA_ALPHA)
    return percent


def set_opacity(api, state, percent):
    """트레이 메뉴와 위젯 버튼이 함께 쓰는 진입점."""
    state["opacity"] = max(30, min(100, int(percent)))
    if api._hwnd:
        apply_opacity(api._hwnd, state["opacity"])
    save_state(state)
    return state["opacity"]


# 앱이 받는 세션 ID 형식(앱의 claude:// 처리기와 같은 규칙). 그 밖의 값은 앱만 띄운다
APP_SESSION_RE = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")


def session_url(app_session_id):
    """앱의 그 세션으로 이동하는 링크. 앱 세션 ID를 모르면 앱만 앞으로 띄운다."""
    if app_session_id and APP_SESSION_RE.match(app_session_id):
        return f"claude://code/continue?session={app_session_id}"
    return "claude://"


def set_scale(api, state, scale):
    """위젯 크기 배율을 50~200% 안으로 맞춰 저장한다. 창 크기는 페이지(widget.js)가 배율을 곱해 set_size로 알린다."""
    state["scale"] = round(max(SCALE_MIN, min(SCALE_MAX, float(scale))), 2)
    save_state(state)
    return state["scale"]


class Api:
    """페이지(widget.js)가 부르는 함수. 밑줄로 시작하지 않는 속성은 pywebview가 페이지에 노출하려고
    안쪽까지 훑으므로, 상태와 창 핸들은 밑줄 이름으로 둔다."""

    def __init__(self, state, on_close=None):
        self._state, self._hwnd, self._on_close = state, None, on_close

    def set_size(self, size, w, h):
        self._state.update(size=size, w=int(w), h=int(h))
        if self._hwnd:
            fit_size(self._hwnd, int(w), int(h))
        save_state(self._state)

    def set_opacity(self, percent):
        return set_opacity(self, self._state, percent)

    def set_scale(self, scale):
        return set_scale(self, self._state, scale)

    def open_app(self, app_session_id=None):
        """세션 줄을 눌렀을 때 Claude 앱의 그 세션으로 이동한다(모르면 앱만 띄움). 실패하면 아무것도 하지 않는다."""
        try:
            os.startfile(session_url(app_session_id))
        except OSError:
            pass

    def get_state(self):
        return {"size": self._state["size"], "opacity": self._state.get("opacity", 100),
                "scale": self._state.get("scale", 1.0)}

    def close(self):
        if self._on_close:
            self._on_close()                                           # 트레이 프로그램: 숨기기
        elif self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)


def set_visible(api, state, visible):
    state["visible"] = bool(visible)
    save_state(state)
    if api._hwnd:
        user32.ShowWindow(api._hwnd, SW_SHOWNA if visible else SW_HIDE)


def destroy(api):
    if api._hwnd:
        user32.PostMessageW(api._hwnd, WM_CLOSE, 0, 0)


def run(window, api, state):
    """창이 뜬 뒤 별도 스레드에서: 위치·크기 맞춤 → 수신기 대기 → 페이지 로드."""
    hwnd = None
    for _ in range(100):
        hwnd = find_hwnd()
        if hwnd:
            break
        time.sleep(0.05)
    if hwnd:
        api._hwnd = hwnd
        hide_from_taskbar(hwnd)
        x, y = state["x"], state["y"]
        saved_ok = x is not None and y is not None and user32.MonitorFromPoint(wintypes.POINT(x, y), 0)
        if not saved_ok:                                               # 처음이거나 그 모니터가 없어졌다
            x, y = default_position(hwnd, state["w"], state["h"])
        user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        fit_size(hwnd, state["w"], state["h"])
        apply_opacity(hwnd, state.get("opacity", 100))
        if not state.get("visible", True):
            user32.ShowWindow(hwnd, SW_HIDE)

    while True:
        try:
            urllib.request.urlopen(BASE + "/api/snapshot", timeout=3).close()
            break
        except OSError:
            time.sleep(3)
    window.load_url(f"{BASE}/web/widget.html?size={state['size']}")


def create(state, on_close=None):
    """창과 api를 만든다. webview.start(run, (window, api, state))로 돌린다."""
    api = Api(state, on_close)
    window = webview.create_window(
        TITLE, html=WAIT_HTML, js_api=api, width=state["w"], height=state["h"],
        frameless=True, easy_drag=True, on_top=True, resizable=False, background_color="#1a1a19",
        min_size=(120, 24),          # 기본값 (200, 100)이면 한 줄 구성(200×30)이 높이 100으로 잘린다
    )
    timer = [None]

    def remember():
        if api._hwnd:
            fit_size(api._hwnd, state["w"], state["h"])                # 배율이 다른 모니터로 옮겼을 때
            state["x"], state["y"] = window_pos(api._hwnd)
            save_state(state)

    def on_moved(*_):
        # 끄는 동안 이벤트가 연달아 오므로 멈춘 뒤 0.5초에 한 번만 처리한다
        if timer[0]:
            timer[0].cancel()
        timer[0] = threading.Timer(0.5, remember)
        timer[0].start()

    window.events.moved += on_moved
    return window, api


def main():
    # 이름 있는 뮤텍스로 중복 실행을 막는다
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "usage-monitor-widget")
    if ctypes.windll.kernel32.GetLastError() == 183:                   # ERROR_ALREADY_EXISTS
        return
    state = load_state()
    state["visible"] = True
    window, api = create(state)
    webview.start(run, (window, api, state))
    del mutex


if __name__ == "__main__":
    main()
