"""大屏 HTTP 服务。进程内单例，只绑 127.0.0.1，关闭后端口不再接受连接。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .snapshot import build_snapshot

DEFAULT_PORT = 18765
_HOST = "127.0.0.1"
_LOCK = threading.Lock()
_current: DashboardServer | None = None


class _HTTP(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _html_bytes() -> bytes:
    path = Path(__file__).resolve().parent / "static" / "index.html"
    return path.read_bytes()


def _handler(rsi_dir: Path, page: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/dashboard", "/dashboard/"}:
                self._send(200, "text/html; charset=utf-8", page)
                return
            if path == "/api/snapshot":
                body = json.dumps(
                    build_snapshot(rsi_dir), ensure_ascii=False,
                ).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found")

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


class DashboardServer:
    def __init__(self, rsi_dir: Path):
        self.rsi_dir = Path(rsi_dir).resolve()
        self.port = 0
        self._httpd: _HTTP | None = None
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, preferred: int) -> None:
        page = _html_bytes()
        handler = _handler(self.rsi_dir, page)
        ports = [0] if preferred <= 0 else [*range(preferred, preferred + 10), 0]
        last: OSError | None = None
        httpd: _HTTP | None = None
        for port in ports:
            try:
                httpd = _HTTP((_HOST, port), handler)
                break
            except OSError as exc:
                last = exc
        if httpd is None:
            raise last or OSError("bind failed")
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="rsi-web-ui", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

    def public(self) -> dict[str, Any]:
        base = f"http://{_HOST}:{self.port}"
        return {
            "listening": True,
            "url": f"{base}/dashboard",
            "snapshot_url": f"{base}/api/snapshot",
        }


def open_dashboard(rsi_dir: Path, *, port: int | None = None) -> dict[str, Any]:
    """已在听同一份 .rsi 时直接返回原地址，否则先停掉旧的再绑端口。"""
    global _current
    preferred = DEFAULT_PORT if port is None else port
    target = Path(rsi_dir).resolve()
    with _LOCK:
        if _current is not None and _current.alive and _current.rsi_dir == target:
            return _current.public()
        if _current is not None:
            _current.stop()
            _current = None
        server = DashboardServer(target)
        try:
            server.start(preferred)
        except Exception:
            server.stop()
            raise
        _current = server
        return server.public()


def close_dashboard() -> dict[str, Any]:
    global _current
    with _LOCK:
        if _current is not None:
            _current.stop()
            _current = None
    return {"listening": False, "url": None, "snapshot_url": None}


def dashboard_status() -> dict[str, Any]:
    with _LOCK:
        if _current is not None and _current.alive:
            return _current.public()
    return {"listening": False, "url": None, "snapshot_url": None}
