@echo off
cd /d "E:\Project\Mt5 Ea"
python -c "from flask import Flask; app = Flask(__name__); app.run(host='127.0.0.1', port=8080, debug=False)"
