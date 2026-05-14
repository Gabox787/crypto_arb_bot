"""
keep_alive.py
Lightweight Flask HTTP server in a background thread.
Fixes WERKZEUG_SERVER_FD error on Render by using werkzeug directly.
"""
 
import logging
import threading
 
from flask import Flask
 
logger = logging.getLogger(__name__)
 
app = Flask(__name__)
 
 
@app.route("/")
def index():
    return "Bot is alive!", 200
 
 
@app.route("/health")
def health():
    return {"status": "ok"}, 200
 
 
def start_keep_alive(host: str = "0.0.0.0", port: int = 10000) -> None:
    """Start Flask in a daemon thread."""
    def _run():
        # Use werkzeug's make_server directly — avoids WERKZEUG_SERVER_FD bug
        from werkzeug.serving import make_server
        srv = make_server(host, port, app)
        logger.info("Keep-alive server listening on http://%s:%d", host, port)
        srv.serve_forever()
 
    thread = threading.Thread(target=_run, name="keep-alive", daemon=True)
    thread.start()
