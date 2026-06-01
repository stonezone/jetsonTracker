"""Local recorder: ffmpeg -c copy of RTSP /1 (main) -> segmented mp4 on NVMe.

Stream copy / remux only (the Orin Nano has no NVENC), so CPU cost is negligible
and the full-quality camera stream is preserved. Segments rotate so a session is
easy to browse + share. Recording is independent of tracking.
"""

import glob
import os
import shutil
import subprocess
import time

REC_DIR = os.environ.get("REC_DIR", "/data/recordings")
RTSP_MAIN = os.environ.get("RTSP_MAIN", "rtsp://192.168.100.88:554/1")


class Recorder:
    def __init__(self):
        self.proc = None
        os.makedirs(REC_DIR, exist_ok=True)

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, segment_time=600):
        if self.is_running():
            return {"ok": True, "already": True}
        ts = time.strftime("%Y%m%d_%H%M%S")
        pattern = os.path.join(REC_DIR, f"rec_{ts}_%03d.mp4")
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", RTSP_MAIN,
            "-c", "copy", "-f", "segment",
            "-segment_time", str(segment_time), "-reset_timestamps", "1",
            "-movflags", "+faststart", pattern,
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "started": True, "pattern": os.path.basename(pattern)}

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
        segs = sorted(glob.glob(os.path.join(REC_DIR, "*.mp4")))
        du = shutil.disk_usage(REC_DIR)
        return {
            "recording": self.is_running(),
            "dir": REC_DIR,
            "segments": len(segs),
            "latest": [os.path.basename(s) for s in segs[-5:]],
            "total_mb": round(sum(os.path.getsize(s) for s in segs) / 1e6, 1) if segs else 0.0,
            "disk_free_gb": round(du.free / 1e9, 1),
        }


if __name__ == "__main__":
    import sys
    r = Recorder()
    print("start:", r.start(segment_time=3600))
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 8.0)
    print("status:", r.status())
    print("stop:", r.stop())
    print("final:", r.status())
