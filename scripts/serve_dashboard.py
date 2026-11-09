"""独立大屏进程：MCP 进程未重启时，用它立刻拿到最新前后端。

用法：.venv/Scripts/python.exe scripts/serve_dashboard.py [port]
Ctrl+C 退出；正常使用时仍推荐 rsi_web_ui（MCP 重启后 18765 即最新）。
"""

import sys
import time
from pathlib import Path

from rsi_boot.dashboard.server import close_dashboard, open_dashboard


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18766
    rsi_dir = Path.cwd() / ".rsi"
    info = open_dashboard(rsi_dir, port=port)
    print(f"dashboard: {info['url']}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        close_dashboard()


if __name__ == "__main__":
    main()
