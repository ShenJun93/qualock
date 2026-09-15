#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.server
import json
import re
from pathlib import Path

MAX_REQUEST_BYTES = 1_048_576
REQUEST_PATH = re.compile(r"/rep[123]/(?:0\.149\.1|0\.150\.0|0\.150\.1|0\.151\.0)/v1/responses")


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    output_path: Path

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, b"ok\n")
        else:
            self._reply(404, b"not found\n")

    def do_POST(self) -> None:
        if REQUEST_PATH.fullmatch(self.path) is None:
            self._reply(404, b"not found\n")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._reply(411, b"content length required\n")
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._reply(400, b"invalid content length\n")
            return
        if length < 1 or length > MAX_REQUEST_BYTES:
            self._reply(413, b"request too large\n")
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, b"invalid json\n")
            return
        tools = payload.get("tools")
        if not isinstance(tools, list):
            self._reply(400, b"missing tools\n")
            return
        names = [
            tool.get("name") or tool.get("function", {}).get("name") or tool.get("type")
            for tool in tools
            if isinstance(tool, dict)
        ]
        record = {"path": self.path, "model": payload.get("model"), "tools": names}
        with self.output_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, separators=(",", ":")) + "\n")
        body = b'{"error":{"message":"qualock managed probe complete","type":"invalid_request_error"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18063)
    parser.add_argument("--bind", default="127.0.0.1")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("", encoding="utf-8")
    handler = type("BoundCaptureHandler", (CaptureHandler,), {"output_path": args.out})
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
