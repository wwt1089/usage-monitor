"""um-hook.exe 의 진입점. Python이 없는 PC에서 Claude Code가 실행하는 hook·statusline.

    um-hook.exe statusline | nudge | post | stop | session

상태줄은 응답마다 불리므로 빨리 떠야 한다. 필요한 모듈 하나만 import한다.
"""
import sys

MODULES = {"statusline": "statusline", "nudge": "usage_prompt_nudge",
           "post": "usage_post_tool", "stop": "usage_stop_guard",
           "session": "session_event"}


def main():
    name = MODULES.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if not name:
        sys.exit("usage: um-hook statusline | nudge | post | stop | session")
    import importlib
    importlib.import_module(f"usage_monitor.hooks.{name}").main()


if __name__ == "__main__":
    main()
