"""바 모니터(1920×480)에 대시보드를 Chrome 키오스크로 띄운다. scripts/kiosk.cmd·kiosk-auto.ps1을 대신한다."""
import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

PROFILE_DIR = "usage-monitor-kiosk"      # 평소 쓰는 Chrome과 섞이지 않는 전용 프로필
BAR_SIZE = (1920, 480)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def displays():
    """(x, y, 너비, 높이) 목록. 이 프로세스의 DPI 인식 기준 좌표다."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def visit(monitor, _dc, _rect, _param):
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        r = info.rcMonitor
        found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumDisplayMonitors(None, None, visit, 0)
    return found


def find_bar():
    return next((d for d in displays() if (d[2], d[3]) == BAR_SIZE), None)


def find_browser():
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LocalAppData")]
    for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
        for root in filter(None, roots):
            p = Path(root) / rel
            if p.exists():
                return p
    return None


def is_running():
    """전용 프로필로 뜬 브라우저가 있는지. 프로필 폴더의 잠금 파일로 본다."""
    lock = Path(os.environ.get("LocalAppData", "")) / PROFILE_DIR / "lockfile"
    try:
        with open(lock, "r+b"):
            return False              # 열리면 잡고 있는 프로세스가 없다
    except FileNotFoundError:
        return False
    except OSError:
        return True


def open_kiosk(url):
    """바 모니터가 있으면 띄우고 (성공 여부, 설명)을 돌려준다."""
    bar = find_bar()
    if not bar:
        sizes = ", ".join(f"{w}x{h}" for _, _, w, h in displays())
        return False, f"1920x480 디스플레이가 없습니다 (연결된 화면: {sizes})"
    browser = find_browser()
    if not browser:
        return False, "Chrome이나 Edge를 찾지 못했습니다"
    if is_running():
        return True, "이미 떠 있습니다"
    profile = Path(os.environ["LocalAppData"]) / PROFILE_DIR
    # --kiosk만으로는 서브 모니터 위치가 잡히지 않아 --window-position을 같이 준다
    subprocess.Popen([
        str(browser), "--kiosk", f"--app={url}", f"--window-position={bar[0]},{bar[1]}",
        f"--window-size={BAR_SIZE[0]},{BAR_SIZE[1]}", f"--user-data-dir={profile}",
        "--no-first-run", "--noerrdialogs", "--disable-session-crashed-bubble", "--disable-features=Translate",
    ])
    return True, f"{bar[0]},{bar[1]}에 띄웠습니다"


# ── 일반 모니터 전체화면(full.html) ─────────────────
FULL_PROFILE_DIR = "usage-monitor-full"   # 키오스크와 따로 둬서 둘을 함께 띄울 수 있다


def full_command(browser, url, profile):
    """전체화면 대시보드 명령. --kiosk 대신 --start-fullscreen이라 F11로 창 모드, Alt+F4로 닫을 수 있다."""
    return [str(browser), "--start-fullscreen", f"--app={url}", f"--user-data-dir={profile}",
            "--no-first-run", "--noerrdialogs", "--disable-session-crashed-bubble", "--disable-features=Translate"]


def open_full(url):
    """주 모니터에 전체화면 대시보드를 띄우고 (성공 여부, 설명)을 돌려준다."""
    browser = find_browser()
    if not browser:
        return False, "Chrome이나 Edge를 찾지 못했습니다"
    subprocess.Popen(full_command(browser, url, Path(os.environ["LocalAppData"]) / FULL_PROFILE_DIR))
    return True, "전체화면 대시보드를 띄웠습니다 (F11 창 모드 · Alt+F4 닫기)"
