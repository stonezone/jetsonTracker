#!/usr/bin/env python3
"""
WAVECAM vision-only testbed entrypoint.

  python run.py                 # uses ./config.yaml
  python run.py my.yaml         # custom config

Bring-up order (see README):
  1) ptz.enabled=false -> open the web UI, confirm it boxes your orange jersey + person
  2) confirm onboard AI-track is OFF
  3) ptz.enabled=true with conservative speeds -> stand in front, verify it follows
     (if it moves the wrong way, flip invert_pan/invert_tilt via the UI or config)
"""
from __future__ import annotations
import sys

import uvicorn

from wavecam.config import load_config
from wavecam.ptz_visca import ViscaIP, NullPtz
from wavecam.camera_http import disable_onboard_ai
from wavecam.pipeline import Pipeline
from wavecam.web import build_app


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(cfg_path)

    # onboard AI off (best effort)
    disable_onboard_ai(cfg.camera_ai)

    # PTZ backend
    if cfg.ptz.enabled:
        ptz = ViscaIP(cfg.ptz.ip, cfg.ptz.port, cfg.ptz.address)
        if cfg.ptz.reset_sequence:
            ptz.reset_sequence()
        ptz.stop()  # ensure stationary at startup
        print(f"[run] PTZ ENABLED -> VISCA {cfg.ptz.ip}:{cfg.ptz.port}")
    else:
        ptz = NullPtz()
        print("[run] PTZ disabled (detection-only). Set ptz.enabled=true when ready.")

    def detector_factory():
        from wavecam.detector import PersonDetector
        print(f"[run] loading YOLO model: {cfg.detector.model}")
        return PersonDetector(cfg.detector)

    pipe = Pipeline(cfg, ptz, detector_factory)
    pipe.start()

    app = build_app(pipe)
    print(f"[run] open the console:  http://<this-host>:{cfg.web.port}/")
    try:
        uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="warning")
    finally:
        pipe.stop()
        ptz.stop()
        ptz.close()


if __name__ == "__main__":
    main()
