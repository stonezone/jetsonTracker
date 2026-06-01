"""Best-effort livestream: ffmpeg remux RTSP /1 -> RTMP (no re-encode; Orin has no NVENC).

Reads the RTMP target from env STREAM_URL, or a gitignored config
config/stream.local.json: {"rtmp_url": "...", "stream_key": "..."}. Does NOT
invent a key — reports "not configured" until Zack adds one. Never blocks
recording/tracking; best-effort with the operator able to restart it.
"""

import json
import os
import subprocess
import time

GIMBAL = os.environ.get("GIMBAL_DIR", "/data/projects/gimbal")
RTSP_MAIN = os.environ.get("RTSP_MAIN", "rtsp://192.168.100.88:554/1")
CONF_PATH = os.environ.get("STREAM_CONF", os.path.join(GIMBAL, "config", "stream.local.json"))


def stream_url():
    u = os.environ.get("STREAM_URL")
    if u:
        return u
    if os.path.exists(CONF_PATH):
        try:
            c = json.load(open(CONF_PATH))
            base = (c.get("rtmp_url") or "").rstrip("/")
            key = c.get("stream_key") or ""
            if base and key and "PASTE" not in key:
                return base + "/" + key
            if base and not key:
                return base
        except Exception:
            pass
    return None


class Streamer:
    def __init__(self):
        self.proc = None
        self.started_at = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def configured(self):
        return stream_url() is not None

    def start(self):
        if self.is_running():
            return {"ok": True, "already": True}
        url = stream_url()
        if not url:
            return {"ok": False, "error": "no stream key configured", "config": CONF_PATH}
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
               "-i", RTSP_MAIN, "-c", "copy", "-f", "flv", url]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started_at = time.time()
        return {"ok": True, "started": True}

    def stop(self):
        if not self.is_running():
            return {"ok": True, "already_stopped": True}
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.proc = None
        return {"ok": True, "stopped": True}

    def status(self):
        return {
            "streaming": self.is_running(),
            "configured": self.configured(),
            "config_path": CONF_PATH,
            "uptime_s": int(time.time() - self.started_at) if (self.is_running() and self.started_at) else 0,
        }
