# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정. 실행: pyinstaller packaging/usage-monitor.spec --noconfirm
# 결과: dist/UsageMonitor/ (폴더형). 실행 파일 셋이 _internal 을 함께 쓴다.
#   UsageMonitor.exe   창 없는 실행 파일. 트레이 프로그램(자동 실행·시작 메뉴가 이것을 부른다)
#   usage-monitor.exe  콘솔 실행 파일. setup · uninstall · status 등 명령
#   um-hook.exe        콘솔 실행 파일. Claude Code가 부르는 statusline·hook. 빨리 뜨도록 필요한 모듈만 담는다
import os
import re

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)
BUILD = os.path.join(ROOT, "build")
os.makedirs(BUILD, exist_ok=True)

version = re.search(r'^version = "([^"]+)"', open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read(), re.M).group(1)
nums = tuple(int(x) for x in (version.split(".") + ["0", "0", "0"])[:4])

# 제품 이름과 버전을 실행 파일 속성에 넣는다(코드 서명 조건이기도 하다)
version_file = os.path.join(BUILD, "version_info.txt")
with open(version_file, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'lepela'),
      StringStruct('FileDescription', 'Usage Monitor'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductName', 'Usage Monitor'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('LegalCopyright', 'Mozilla Public License 2.0'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")

# 아이콘은 트레이 아이콘과 같은 그림으로 만든다
icon_file = os.path.join(BUILD, "usage-monitor.ico")
import sys
sys.path.insert(0, ROOT)
from usage_monitor.tray import make_icon
make_icon(None).save(icon_file, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

HOOK_MODULES = [f"usage_monitor.hooks.{m}" for m in
                ("statusline", "usage_hook_common", "usage_prompt_nudge", "usage_post_tool", "usage_stop_guard",
                 "session_event")]

app = Analysis(
    [os.path.join(SPECPATH, "entry_app.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "usage_monitor", "web"), "usage_monitor/web")],
    hiddenimports=collect_submodules("uvicorn") + HOOK_MODULES,
    excludes=["tkinter"],
)
hook = Analysis(
    [os.path.join(SPECPATH, "entry_hook.py")],
    pathex=[ROOT],
    hiddenimports=HOOK_MODULES,
    excludes=["tkinter", "uvicorn", "fastapi", "pydantic", "webview", "pystray", "PIL", "sqlite3", "asyncio"],
)

app_pyz = PYZ(app.pure)
hook_pyz = PYZ(hook.pure)
common = dict(exclude_binaries=True, version=version_file, icon=icon_file, upx=False)

tray_exe = EXE(app_pyz, app.scripts, [], name="UsageMonitor", console=False, **common)
cli_exe = EXE(app_pyz, app.scripts, [], name="usage-monitor", console=True, **common)
hook_exe = EXE(hook_pyz, hook.scripts, [], name="um-hook", console=True, **common)

COLLECT(tray_exe, cli_exe, hook_exe, app.binaries, app.datas, hook.binaries, hook.datas, name="UsageMonitor", upx=False)
