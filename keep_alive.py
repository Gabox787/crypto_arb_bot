"""
keep_alive.py
Lightweight Flask HTTP server that runs in a background thread.
Render's free tier spins down inactive services — this endpoint
lets an external uptime monitor (e.g. UptimeRobot) ping the bot
every ~5 minutes to keep it awake.
"""

import logging
import threading

from flask import Flask

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return "✅ Bot is alive!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def start_keep_alive(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start Flask in a daemon thread so it doesn't block the bot."""
    def _run():
        # Suppress Flask's default startup banner to keep logs clean
        import os
        os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")
        app.run(host=host, port=port, use_reloader=False)

    thread = threading.Thread(target=_run, name="keep-alive", daemon=True)
    thread.start()
    logger.info("Keep-alive server started on http://%s:%d", host, port)
