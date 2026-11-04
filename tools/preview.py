import asyncio
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import config
import dbx
import web
from d1shim import Binding

MIME = {".css": "text/css", ".png": "image/png", ".ico": "image/x-icon", ".js": "text/javascript", ".webmanifest": "application/manifest+json"}
dbx.use(Binding(os.path.join(ROOT, "schema.sql")))
config.DEMO_LOGIN = os.environ.get("DEMO_LOGIN", "true") == "true"
loop = asyncio.new_event_loop()
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def serve(self, method):
        path = self.path.split("?")[0]
        local = os.path.join(ROOT, "public", path.lstrip("/"))
        if method == "GET" and path != "/" and os.path.isfile(local):
            data = open(local, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(os.path.splitext(local)[1], "application/octet-stream"))
            self.end_headers()
            self.wfile.write(data)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        host = self.headers.get("Host", "localhost")
        with lock:
            result = loop.run_until_complete(web.handle(method, "http://" + host + self.path, self.headers.get("Cookie", ""), body))
        self.send_response(result.status)
        for name, value in result.headers:
            self.send_header(name, value)
        for cookie in result.cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(result.body.encode("utf-8"))

    def do_GET(self):
        self.serve("GET")

    def do_POST(self):
        self.serve("POST")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    print("Preview on http://localhost:%d  (in-memory database, resets on restart)" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
