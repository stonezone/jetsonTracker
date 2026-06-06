"""Start/stop vision/vision_follow.py as a managed subprocess + expose its readout.

Vision-only PTZ follow (YOLO person + orange color, no GPS) for the yard MVP. A
separate process so it survives a dashboard restart; SIGTERM -> vision_follow's
finally restores the camera to home. Parses the `[follow] src=...` status lines
for a compact live readout in the UI.
"""

import collections
import os
import subprocess
import threading
import time

GIMBAL = os.environ.get("GIMBAL_DIR", "/data/projects/gimbal")
FOLLOW = os.path.join(GIMBAL, "vision", "vision_follow.py")


class FollowRunner:
    def __init__(self):
        self.proc = None
        self.lines = collections.deque(maxlen=80)
        self._t = None
        self.last_returncode = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def _remember_exit(self):
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is not None:
            self.last_returncode = rc
        return rc

    def start(self, no_yolo=False, no_color=False, target_frac=None):
        if self.is_running():
            return {"ok": True, "already": True}
        cmd = ["python3", FOLLOW]
        if no_yolo:
            cmd.append("--no-yolo")
        if no_color:
            cmd.append("--no-color")
        if target_frac is not None:
            cmd += ["--target-frac", str(float(target_frac))]
        self.lines.clear()
        self.proc = subprocess.Popen(
            cmd, cwd=GIMBAL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()
        self.last_returncode = None
        time.sleep(0.25)
        rc = self._remember_exit()
        if rc is not None:
            return {
                "ok": False,
                "started": False,
                "error": f"vision follow exited early with code {rc}",
                "cmd": " ".join(cmd[1:]),
                "lines": list(self.lines)[-12:],
            }
        return {"ok": True, "started": True, "cmd": " ".join(cmd[1:])}

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
        except Exception:
            pass

    def stop(self):
        if not self.is_running():
            return {"ok": True, "already_stopped": True}
        self.proc.terminate()    # vision_follow SIGTERM -> restores camera home (~7s)
        try:
            self.proc.wait(timeout=12)
        except Exception:
            self.proc.kill()
        self.proc = None
        return {"ok": True, "stopped": True}

    def status(self):
        rc = self._remember_exit()
        last = ""
        for ln in reversed(self.lines):
            if ln.startswith("[follow] src="):
                last = ln[len("[follow] "):]
                break
        return {
            "running": self.is_running(),
            "returncode": rc if rc is not None else self.last_returncode,
            "error": (
                f"vision follow exited with code {rc if rc is not None else self.last_returncode}"
                if not self.is_running() and self.last_returncode not in (None, 0)
                else ""
            ),
            "last": last,
            "lines": list(self.lines)[-12:],
        }
