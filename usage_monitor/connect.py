"""Claude Code 설정(~/.claude/settings.json)에 usage-monitor를 연결하고 해제한다.

    python -m usage_monitor status | connect | disconnect

연결이 하는 일
  1. hook·statusline 스크립트를 고정 위치(~/.claude/usage-monitor/hooks/)에 복사한다.
     설정에는 이 위치를 적는다. 프로그램 폴더를 옮기거나 지워도 설정이 깨지지 않는다
  2. 설정 파일을 백업한 뒤 세 가지를 등록한다
       env        OTel 로그를 로컬 수신기로 보낸다
       statusLine CLI 세션의 한도 관측
       hooks      Desktop 세션의 get_usage 관측 (UserPromptSubmit · PostToolUse · Stop)
                  세션 상태 기록 (SESSION_EVENTS 7개, async: 기록만 하므로 세션을 기다리게 하지 않는다)
     한 이벤트에 우리 그룹이 여럿일 수 있다(예: UserPromptSubmit = nudge + 세션 상태)

남의 설정은 건드리지 않는다
  - 이미 다른 statusLine이 있으면 그대로 두고 알린다
  - OTel을 이미 다른 곳으로 보내고 있으면 env를 건드리지 않고 알린다
  - 다른 hook은 그대로 두고 우리 것만 넣고 뺀다. 우리 것은 명령에 든 MARKER로 알아본다
  - 해제는 우리가 넣은 값이 그대로일 때만 지운다(넣은 값은 manifest에 적어 둔다)
"""
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

MARKER = "usage-monitor/hooks/"          # 우리 명령에만 들어 있는 경로 조각 (Python 스크립트 방식)
HOOK_EXE = "um-hook.exe"                 # 실행 파일 방식. 이 이름도 우리 명령으로 본다
FROZEN = getattr(sys, "frozen", False)   # PyInstaller로 묶인 실행 파일 안인지
EXE_ARGS = {"usage_prompt_nudge.py": "nudge", "usage_post_tool.py": "post", "usage_stop_guard.py": "stop",
            "session_event.py": "session"}
HOOK_FILES = ("statusline.py", "usage_hook_common.py", "usage_prompt_nudge.py",
              "usage_post_tool.py", "usage_stop_guard.py", "session_event.py")
TOOL_MATCHER = "mcp__ccd_session_mgmt__get_usage"
# 세션 상태 hook을 거는 이벤트. SessionEnd는 앱이 쉬는 세션을 정리할 때도 와서 쓰지 않는다
# PermissionRequest는 auto 모드에서 승인 창 없이도 와서 쓰지 않는다(승인 대기는 Notification으로 안다)
SESSION_EVENTS = ("UserPromptSubmit", "Notification", "PostToolUse",
                  "PostToolUseFailure", "Stop", "StopFailure", "SessionStart")
SOURCE_HOOKS = Path(__file__).resolve().parent / "hooks"


def default_paths():
    home = Path(os.environ.get("USAGE_MONITOR_HOME") or Path.home() / ".claude" / "usage-monitor")
    return {
        "settings": Path.home() / ".claude" / "settings.json",
        "hooks_dir": home / "hooks",
        "backup_dir": home / "backup",
        "manifest": home / "state" / "connect.json",
    }


def _cmd(python, script, flags):
    """Claude Code는 Windows에서 Git Bash로 명령을 돌린다. 역슬래시가 이스케이프로 먹히지 않게 슬래시로 쓴다."""
    exe = Path(python).as_posix()
    exe = f'"{exe}"' if " " in exe else exe
    return f'{exe} {flags} "{Path(script).as_posix()}"'


def _exe_cmd(exe, arg):
    return f'"{Path(exe).as_posix()}" {arg}'


def desired(hooks_dir, python, port=4318, host=None, hook_exe=None):
    """등록할 값. hook_exe가 있으면 실행 파일 방식, 없으면 hooks_dir의 스크립트를 python으로 돌린다.
    hooks는 {이벤트: [그룹, …]} 이다."""
    if hook_exe:
        hook = lambda name: {"type": "command", "command": _exe_cmd(hook_exe, EXE_ARGS[name]), "timeout": 10}
        statusline = _exe_cmd(hook_exe, "statusline")
    else:
        hook = lambda name: {"type": "command", "command": _cmd(python, hooks_dir / name, "-S"), "timeout": 10}
        statusline = _cmd(python, hooks_dir / "statusline.py", "-S -I")
    # 1단계: 한도 관측용 그룹(기존)
    hooks = {
        "UserPromptSubmit": [{"hooks": [hook("usage_prompt_nudge.py")]}],
        "PostToolUse": [{"matcher": TOOL_MATCHER, "hooks": [hook("usage_post_tool.py")]}],
        "Stop": [{"hooks": [hook("usage_stop_guard.py")]}],
    }
    # 2단계: 세션 상태 그룹. 모든 경우에 걸리도록 matcher를 두지 않고, 백그라운드로 돌린다
    for event in SESSION_EVENTS:
        hooks.setdefault(event, []).append({"hooks": [dict(hook("session_event.py"), **{"async": True})]})
    return {
        "env": {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"http://127.0.0.1:{port}/v1/logs",
            "OTEL_RESOURCE_ATTRIBUTES": f"host.name={host or os.environ.get('COMPUTERNAME') or socket.gethostname()}",
        },
        "statusLine": {"type": "command", "command": statusline},
        "hooks": hooks,
    }


def _ours(command):
    return MARKER in (command or "") or HOOK_EXE in (command or "")


def _is_ours(entry):
    return any(_ours(h.get("command")) for h in entry.get("hooks") or [])


def _statusline_is_ours(settings):
    return _ours((settings.get("statusLine") or {}).get("command"))


def default_hook_exe():
    """실행 파일로 설치됐을 때 um-hook.exe 의 위치. 아니면 None."""
    return Path(sys.executable).with_name(HOOK_EXE) if FROZEN else None


def _load(path):
    if not path.exists():
        return {}
    # 메모장·PowerShell로 저장한 파일은 UTF-8 BOM으로 시작할 수 있다
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)          # 쓰다 끊겨도 원본이 반쯤 쓰인 채 남지 않는다


def _backup(paths):
    if not paths["settings"].exists():
        return None
    paths["backup_dir"].mkdir(parents=True, exist_ok=True)
    dest = paths["backup_dir"] / f"settings.json.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(paths["settings"], dest)
    return dest


def status(paths=None):
    paths = paths or default_paths()
    s = _load(paths["settings"])
    env = s.get("env") or {}
    endpoint = env.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    hooks = s.get("hooks") or {}
    return {
        "otel": "ours" if endpoint and "127.0.0.1" in endpoint and "/v1/logs" in endpoint
                else ("other" if endpoint or env.get("OTEL_LOGS_EXPORTER") else "none"),
        "statusline": "ours" if _statusline_is_ours(s) else ("other" if s.get("statusLine") else "none"),
        "hooks": sorted(ev for ev, entries in hooks.items() if any(_is_ours(e) for e in entries or [])),
        "scripts": default_hook_exe().exists() if FROZEN
                   else all((paths["hooks_dir"] / f).exists() for f in HOOK_FILES),
    }


def connect(paths=None, python=None, port=4318, source_hooks=SOURCE_HOOKS, hook_exe=None):
    """등록하고, 무엇을 했고 무엇을 건너뛰었는지 돌려준다."""
    paths = paths or default_paths()
    python = python or sys.executable.replace("pythonw.exe", "python.exe")
    hook_exe = hook_exe or default_hook_exe()
    report = {"backup": None, "done": [], "skipped": []}

    if hook_exe:
        report["done"].append(f"hook 실행 파일 → {hook_exe}")
    else:
        paths["hooks_dir"].mkdir(parents=True, exist_ok=True)
        for name in HOOK_FILES:
            shutil.copy2(source_hooks / name, paths["hooks_dir"] / name)
        report["done"].append(f"스크립트 복사 → {paths['hooks_dir']}")

    s = _load(paths["settings"])
    backup = _backup(paths)
    report["backup"] = str(backup) if backup else None
    want = desired(paths["hooks_dir"], python, port, hook_exe=hook_exe)
    manifest = {"env": {}, "statusLine": False}

    env = s.setdefault("env", {})
    endpoint = env.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    exporter = env.get("OTEL_LOGS_EXPORTER")
    foreign = (endpoint and endpoint != want["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"]) or \
              (exporter and exporter != "otlp")
    if foreign:
        report["skipped"].append("OTel: 이미 다른 곳으로 보내는 설정이 있어 그대로 둠. 토큰·비용이 수집되지 않음")
    else:
        for k, v in want["env"].items():
            if env.get(k) != v:
                env[k] = v
            manifest["env"][k] = v
        report["done"].append("OTel 로그 → 로컬 수신기")
    if not env:
        s.pop("env")

    if s.get("statusLine") and not _statusline_is_ours(s):
        report["skipped"].append("statusLine: 기존 설정이 있어 그대로 둠. CLI 세션의 한도 관측이 들어오지 않음")
    else:
        s["statusLine"] = want["statusLine"]
        manifest["statusLine"] = True
        report["done"].append("statusLine")

    hooks = s.setdefault("hooks", {})
    # 예전 버전이 넣었지만 이제 쓰지 않는 이벤트에서 우리 그룹을 뺀다(남의 그룹은 그대로)
    for event in [e for e in hooks if e not in want["hooks"]]:
        kept = [e for e in hooks[event] or [] if not _is_ours(e)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    for event, entries in want["hooks"].items():
        # 남의 그룹은 앞에 그대로 두고, 우리 그룹은 지운 뒤 새로 넣는다(다시 연결해도 중복되지 않는다)
        hooks[event] = [e for e in hooks.get(event) or [] if not _is_ours(e)] + entries
    report["done"].append("hooks: " + ", ".join(want["hooks"]))

    _save(paths["settings"], s)
    _save(paths["manifest"], manifest)
    return report


def disconnect(paths=None):
    paths = paths or default_paths()
    report = {"backup": None, "done": [], "skipped": []}
    if not paths["settings"].exists():
        return report
    s = _load(paths["settings"])
    backup = _backup(paths)
    report["backup"] = str(backup) if backup else None
    manifest = _load(paths["manifest"]) if paths["manifest"].exists() else {"env": {}, "statusLine": False}

    hooks = s.get("hooks") or {}
    for event in list(hooks):
        kept = [e for e in hooks[event] or [] if not _is_ours(e)]
        if len(kept) != len(hooks[event] or []):
            report["done"].append(f"hook 제거: {event}")
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    if "hooks" in s and not hooks:
        s.pop("hooks")

    if _statusline_is_ours(s):
        s.pop("statusLine")
        report["done"].append("statusLine 제거")

    env = s.get("env") or {}
    for k, v in (manifest.get("env") or {}).items():
        if env.get(k) == v:
            env.pop(k)
        elif k in env:
            report["skipped"].append(f"env {k}: 연결 뒤에 값이 바뀌어 그대로 둠")
    if manifest.get("env"):
        report["done"].append("OTel env 제거")
    if "env" in s and not env:
        s.pop("env")

    _save(paths["settings"], s)
    shutil.rmtree(paths["hooks_dir"], ignore_errors=True)
    paths["manifest"].unlink(missing_ok=True)
    return report


def main(argv=None):
    action = (argv or sys.argv[1:] or ["status"])[0]
    if action == "status":
        result = status()
    elif action == "connect":
        result = connect()
    elif action == "disconnect":
        result = disconnect()
    else:
        sys.exit("사용: python -m usage_monitor status | connect | disconnect")
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
