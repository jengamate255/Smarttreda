@echo off
cd /d "E:\Project\Mt5 Ea"
python -c "from http.server import HTTPServer, BaseHTTPRequestHandler; class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args):
        pass
HTTPServer(('127.0.0.1', 8080), H).serve_forever()"
