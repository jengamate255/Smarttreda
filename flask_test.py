import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return 'Hello from Flask'

print('Starting Flask app on http://127.0.0.1:8080')
app.run(host='127.0.0.1', port=8080, debug=False)
