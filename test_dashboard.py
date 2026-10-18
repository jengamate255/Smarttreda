import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = '<html><body><h1>Dashboard Test</h1></body></html>'
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    def log_message(self, format, *args):
        pass

try:
    server = HTTPServer(('127.0.0.1', 9999), Handler)
    print('Server started on http://127.0.0.1:9999')
    server.serve_forever()
except Exception as e:
    print('Error:', e)
