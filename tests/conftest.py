"""Shared fixtures: a tiny HTTP server that mimics the go-httpbin endpoints used by the
examples, so the integration tests run real Tavern checks without network access."""

from __future__ import annotations

import base64
import json
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep pytest output clean
        pass

    def _json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path == "/get":
            self._json(200, {"args": parse_qs(url.query), "url": self.path, "headers": {}})
        elif parts[0] == "status" and len(parts) == 2:
            self.send_response(int(parts[1]))
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif parts[0] == "basic-auth" and len(parts) == 3:
            expected = "Basic " + base64.b64encode(f"{parts[1]}:{parts[2]}".encode()).decode()
            if self.headers.get("Authorization") == expected:
                self._json(200, {"authenticated": True, "user": parts[1]})
            else:
                self._json(401, {"authenticated": False, "user": ""})
        elif url.path == "/uuid":
            self._json(200, {"uuid": str(uuid.uuid4())})
        elif url.path == "/bearer":
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            if scheme == "Bearer" and token:
                self._json(200, {"authenticated": True, "token": token})
            else:
                self._json(401, {"authenticated": False, "token": ""})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/post":
            self._json(200, {"data": body.decode(), "json": json.loads(body or b"null")})
        else:
            self._json(404, {"error": "not found"})


@pytest.fixture(scope="session")
def httpbin() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def write(tmp_path: Path):
    """Write a file under tmp_path and return its path."""

    def _write(name: str, content: str) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write
