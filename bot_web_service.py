from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"re:start VK bot is running\n"
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run_http_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"health_server_started port={port}", flush=True)
    server.serve_forever()


def run_vk_bot():
    import vk_re_start_bot_cloud

    vk_re_start_bot_cloud.main()


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_vk_bot, daemon=True)
    bot_thread.start()
    run_http_server()
