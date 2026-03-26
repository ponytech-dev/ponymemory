"""HTTP health check endpoint for PonyMemory Worker."""
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from db import init_db, count_by_status
from embedder import check_qdrant_health, check_bge_m3_health

PORT = 47777
_start_time = time.time()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        conn = init_db()
        status = {
            "worker": "running",
            "uptime_seconds": round(time.time() - _start_time),
            "qdrant": check_qdrant_health(),
            "bge_m3": check_bge_m3_health(),
            "queue_pending": count_by_status(conn, "pending"),
            "queue_failed": count_by_status(conn, "failed"),
            "queue_processing": count_by_status(conn, "processing"),
        }
        conn.close()

        body = json.dumps(status, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress default logging


def start_health_server():
    """Start health server in a daemon thread."""
    server = HTTPServer(("127.0.0.1", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
