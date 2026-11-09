"""大屏 HTTP 服务。进程内单例，只绑 127.0.0.1，关闭后端口不再接受连接。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api import memory_detail, memory_list, recall_events, review
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
            parsed = urlparse(self.path)
            path = parsed.path
            if path in {"/", "/dashboard", "/dashboard/"}:
                self._send(200, "text/html; charset=utf-8", page)
                return
            if path == "/api/snapshot":
                self._json(200, build_snapshot(rsi_dir))
                return
            if path == "/api/memories":
                query = parse_qs(parsed.query)
                items = memory_list(
                    rsi_dir,
                    category=query.get("category", [""])[0],
                    status=query.get("status", [""])[0],
                )
                self._json(200, {"items": items})
                return
            if path == "/api/recalls":
                self._json(200, {"events": recall_events(rsi_dir)})
                return
            if path.startswith("/api/memory/"):
                detail = memory_detail(rsi_dir, path.rsplit("/", 1)[-1])
                if detail is None:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, detail)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/review":
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 65536:
                self._json(400, {"error": "bad body"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "bad json"})
                return
            ids = body.get("ids") if isinstance(body, dict) else None
            action = str(body.get("action") or "") if isinstance(body, dict) else ""
            if (
                not isinstance(ids, list)
                or not ids
                or not all(isinstance(item, str) for item in ids)
                or action not in ("approve", "reject")
            ):
                self._json(400, {"error": "需要 ids 数组与 action=approve|reject"})
                return
            try:
                result = review(rsi_dir, ids, action)
            except Exception as exc:  # 审批异常不拖垮大屏服务
                self._json(500, {"error": f"review failed: {exc.__class__.__name__}"})
                return
            self._json(200, {"status": "success", **result})

        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, "application/json; charset=utf-8", body)

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
