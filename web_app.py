# -*- coding: utf-8 -*-
"""
God Indicator v3.0 — Cloud Web App
Flask backend that wraps GodWatcherEngine and serves a live web dashboard.
Deploy to Render.com for free 24/7 operation.
"""

from flask import Flask, jsonify, render_template, request
import threading, os, sys, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from god_engine import GodWatcherEngine, PAIR_CONFIGS, SignalType

app = Flask(__name__)

# ── Global Engine Instance ────────────────────────────────────────────────────
_engine: GodWatcherEngine = None

def get_engine() -> GodWatcherEngine:
    global _engine
    if _engine is None:
        ntfy  = os.environ.get("NTFY_TOPIC", "god-indicator")
        ci    = int(os.environ.get("CHECK_INTERVAL", "300"))
        cool  = int(os.environ.get("COOLDOWN_MIN",   "30"))
        stars = int(os.environ.get("MIN_STARS",       "3"))
        _engine = GodWatcherEngine(
            ntfy_topic=ntfy, check_interval=ci,
            cooldown_minutes=cool, min_stars=stars
        )
    return _engine

# Auto-start engine when server boots
def _auto_start():
    eng = get_engine()
    eng.start()

threading.Thread(target=_auto_start, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/health")
def health():
    """UptimeRobot pings this to keep Render awake."""
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

@app.route("/api/status")
def api_status():
    eng = get_engine()
    statuses = {}
    with eng._lock:
        for name, s in eng.statuses.items():
            statuses[name] = {
                "name":            s.name,
                "price":           s.price,
                "cpt":             s.cpt,
                "consec":          s.consec,
                "htf":             s.htf,
                "is_narrow":       s.is_narrow,
                "signal":          s.signal.value if s.signal else None,
                "stars":           s.confluence_score,
                "last_check_time": s.last_check_time,
                "error":           s.error,
                "is_enabled":      s.is_enabled,
                "set_number":      s.set_number,
                "cooldown_remaining": s.cooldown_remaining,
            }
    with eng._log_lock:
        logs = [{"text": t, "tag": tag} for t, tag in eng.log_lines[-40:]]

    return jsonify({
        "running":      eng.running,
        "scan_running": eng._scan_running,
        "statuses":     statuses,
        "logs":         logs,
        "timestamp":    datetime.utcnow().isoformat(),
    })

@app.route("/api/start", methods=["POST"])
def api_start():
    get_engine().start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    get_engine().stop()
    return jsonify({"ok": True})

@app.route("/api/force-scan", methods=["POST"])
def api_force_scan():
    get_engine().force_scan()
    return jsonify({"ok": True})

@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(silent=True) or {}
    eng  = get_engine()
    if "cooldown_minutes" in data:
        eng.cooldown_minutes = int(data["cooldown_minutes"])
    if "min_stars" in data:
        eng.min_stars = int(data["min_stars"])
    return jsonify({"ok": True})

@app.route("/api/pair/<name>/toggle", methods=["POST"])
def api_pair_toggle(name):
    data    = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    get_engine().set_pair_enabled(name, enabled)
    return jsonify({"ok": True})

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
