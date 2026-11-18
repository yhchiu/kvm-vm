"""TLS test receiver, running outside the Central VM during the DR drill."""

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import ssl
import time


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # The request path is a capability. Never log it.

    def authorized(self):
        return self.client_address[0] == "172.30.0.12" and hmac.compare_digest(
            self.path, "/" + Path("path").read_text().strip()
        )

    def do_GET(self):
        self.send_response(204 if self.authorized() else 403)
        self.end_headers()

    def do_POST(self):
        if not self.authorized():
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 4096:
            self.send_error(400)
            return
        event = json.loads(self.rfile.read(length))
        with Path("receipts").open("a") as out:
            print(
                json.dumps({"id": event["id"], "status": event["status"], "received": time.time()}),
                file=out,
            )
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("172.30.0.11", 19443), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("cert", "key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
