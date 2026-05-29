"""
Local HTTP server for running the Ascend Proxy without deploying to AWS.

This is the bridge between the ascendai-bridge binary and the target chatbot.
The bridge calls POST /chat, the server forwards to the appropriate adapter,
and returns the bot response.

Usage:
    python local_server.py              # Starts on port 8089 (bridge default)
    python local_server.py 9000         # Custom port

Test with:
    curl -X POST http://localhost:8089/chat \\
      -H 'Content-Type: application/json' \\
      -d '{"prompt": "What can you help me with?", "adapter": "browser", "config_name": "acme"}'

Then point the bridge at it:
    target_app:
      url: http://localhost:8089/chat
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from lambda_function import lambda_handler

KEEPALIVE_INTERVAL = 10  # seconds between whitespace keepalive chunks


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread — required for concurrent bridge workers."""
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"

        # Send headers immediately so the bridge's header-wait timer is satisfied
        # before the adapter starts (slow multi-agent bots take 30–90s to respond).
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Send a whitespace chunk every KEEPALIVE_INTERVAL seconds while the adapter
        # waits for the bot. Leading whitespace is valid JSON, so the bridge can still
        # parse the final concatenated response. This prevents "Client.Timeout while
        # reading body" on bridges with short HTTP timeouts (e.g. ascendai-bridge ~30s).
        wfile_lock = threading.Lock()
        done = threading.Event()

        def keepalive():
            while not done.wait(KEEPALIVE_INTERVAL):
                with wfile_lock:
                    try:
                        self.wfile.write(b"1\r\n \r\n")
                        self.wfile.flush()
                    except Exception:
                        break

        ka = threading.Thread(target=keepalive, daemon=True)
        ka.start()

        event = {"body": body, "path": self.path}
        result = lambda_handler(event, None)

        done.set()
        ka.join()

        # On adapter error, surface it as a response field so Ascend captures it
        result_body = result["body"]
        if result["statusCode"] not in (200, 201):
            body_dict = json.loads(result_body)
            error_msg = body_dict.get("error", f"Adapter error {result['statusCode']}")
            result_body = json.dumps({
                "response": f"[ERROR] {error_msg}",
                "{{ RESPONSE }}": f"[ERROR] {error_msg}",
            })

        with wfile_lock:
            chunk = result_body.encode("utf-8")
            self.wfile.write(f"{len(chunk):x}\r\n".encode())
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n0\r\n\r\n")
            self.wfile.flush()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "service": "ascend-proxy"}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress default per-request logging (Lambda handler logs its own)
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    server = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"Ascend Proxy running on http://localhost:{port}")
    print(f"  POST /chat  — send a prompt to the configured chatbot")
    print(f"  GET  /      — health check")
    print()
    print(f"Connect the bridge:")
    print(f"  target_app:")
    print(f"    url: http://localhost:{port}/chat")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
