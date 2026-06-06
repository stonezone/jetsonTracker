"""Start/stop run_tracker.py as a managed subprocess and expose its live readout.

The tracker is a separate process so it keeps running if the dashboard restarts.
Stop sends SIGTERM, which run_tracker handles gracefully (its finally block
restores the camera to home).
"""

import collections
import os
import subprocess
import threading

GIMBAL = os.environ.get("GIMBAL_DIR", "/data/projects/gimbal")
POSE = os.path.join(GIMBAL, "config", "camera_pose.json")


class TrackerRunner:
    def __init__(self):
        self.proc = None
        self.lines = collections.deque(maxlen=80)
        self._t = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mock_camera=False):
        if self.is_running():
            return {"ok": True, "already": True}
        cmd = ["python3", os.path.join(GIMBAL, "run_tracker.py")]
        cmd += (["--pose", POSE] if os.path.exists(POSE) else ["--sim-cal"])
        if mock_camera:
            cmd.append("--mock-camera")
        self.lines.clear()
        self.proc = subprocess.Popen(
            cmd, cwd=GIMBAL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()
        return {"ok": True, "started": True, "pose": os.path.exists(POSE)}

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
        except Exception:
            pass

    def stop(self):
        if not self.is_running():
            return {"ok": True, "already_stopped": True}
        self.proc.terminate()   # run_tracker handles SIGTERM -> restores camera
        try:
            self.proc.wait(timeout=8)
        except Exception:
            self.proc.kill()
        self.proc = None
        return {"ok": True, "stopped": True}

    def status(self):
        return {
            "running": self.is_running(),
            "pose_exists": os.path.exists(POSE),
            "lines": list(self.lines)[-12:],
        }
