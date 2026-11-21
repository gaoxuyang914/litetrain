"""Tiny local webhook receiver for manual demos.

Usage:
    .venv/Scripts/python scripts/webhook_receiver.py [--port 9099] [--secret ...]

The receiver prints event id, signature headers and the JSON body to the
console. It deliberately has no dependencies outside the standard library.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "LiteTrainWebhookDemo/1.0"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            print(
                json.dumps(
                    {
                        "event": payload.get("event"),
                        "delivery": self.headers.get("X-LiteTrain-Delivery"),
                        "signature": self.headers.get("X-LiteTrain-Signature"),
                        "timestamp": self.headers.get("X-LiteTrain-Timestamp"),
                        "body": payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
        except Exception:
            print(body.decode("utf-8", errors="replace"), flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):  # noqa: ANN002
        print("webhook:", fmt % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9099)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"webhook receiver listening on http://127.0.0.1:{args.port}/hook", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
